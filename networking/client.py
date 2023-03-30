import socket
import sys
import threading
import json
import time
import haversine as hs
import signal
from haversine import Unit
from datetime import datetime
from gps import *
from icao_nnumber_converter_us import n_to_icao, icao_to_n


#global filtering distance (miles)
global filtering_distance_max_miles 
filtering_distance_max_miles = 12.5
filtering_distance_min_miles = 0.1

#current lat and lon of raspberry pi (used for filtering)
global cur_location
cur_location = (38.895616, -77.044122) #hardcoded at start (Wash, DC) (lat, lon)

#configure gpsd
gpsd = gps(mode=WATCH_ENABLE|WATCH_NEWSTYLE)

#serve IP and port
ip = '10.0.0.166'
port = 55555

#if xr client disconnects, a lock on sending new data will start until the client reconnects
global clientLock
clientLock = False

global sendLock
sendLock = False

#thread to listen for incoming messages
def listen():
    global filtering_distance_max_miles
    global clientLock
    while True:
        try:
            msgsize = int(client.recv(1).decode())
            msg = bytearray()
            while len(msg) < msgsize :
                packet = client.recv(msgsize - len(msg)) # Receieve the incoming message from recv_client
                msg.extend(packet)
            data = msg.decode()
            if  data == 'stop':
                clientLock = True
            elif data == 'strt':
                clientLock = False
            else:
                filtering_distance_max_miles = float(data) # Update the filtering distance based on data from headset
        except socket.error:
            print('Failed to recieve data')

#sends json data over the 
def send_json(json_str):
    global sendLock
    #Lock sending resource until thread has fully sent a message
    while sendLock: pass
    sendLock = True
    msgsize = len(json_str)
    # msgsize must be 3 digits long to fit server protocol, any very short or very long JSON strings are dropped
    if msgsize < 1000 or msgsize > 99:
        print('Sending ' +  json_str + '\n')
        client.sendall(bytes(str(msgsize), encoding= 'utf-8')) # send message size prior to sending json (assuming the message size is 3 digits)
        client.sendall(bytes(json_str, encoding = 'utf-8')) # send JSON message
    else:
        print("JSON String Too Large or Small: not sending string")
    sendLock = False

#Polls GPS data and sends through socket
def send_gps_data():
    global cur_location
    nx = gpsd.next()
                
    if nx['class'] == 'TPV':
        altitude = getattr(nx, 'alt', 0)
        track = getattr(nx, 'track', 0)
        speed = getattr(nx, 'speed', 0)
        latitude = getattr(nx,'lat', 38.895616)
        longitude = getattr(nx,'lon', -77.044122)
        climb = getattr(nx, 'climb', 0)
        time = '0.0'
        icao = 'USERCRAFT'

        #set current location to new GPS
        if (latitude != 0 and longitude != 0):
            cur_location = (latitude, longitude)

        gps_dict = dict({'alt': altitude, 'track': track, 'speed':speed, 'lon':longitude, 'lat': latitude, 'climb': climb, 'time': time, 'icao': icao, 'isGPS':'true'})
        json_gps = json.dumps(gps_dict)
        send_json(json_gps)
        gpsd.next()
    else:
        gpsd.next()
        print('GPS Poll Failed')

#Controls the GPS polling thread
def run_gps_thread():
    global clientLock
    while True:
        while clientLock: pass
        send_gps_data()
        time.sleep(1.0)

#Parses aircraft JSON data and sends valid aircraft through socket
def send_aircraft_data(path):
    global cur_location
    global filtering_distance_max_miles
    f = open(path, 'r+')
    f_json = json.load(f)
    for aircraft in f_json['aircraft']:
        #data has been updated within last second and lat and lon exists
        if aircraft['seen'] < 1.0 and 'lat' in aircraft and 'lon' in aircraft:
            aircraft_location = (aircraft['lat'], aircraft['lon'])
            rel_dist_miles = hs.haversine(aircraft_location, cur_location, unit = Unit.MILES)
            #airplane is within maximum and minimum filtering distance
            if rel_dist_miles < filtering_distance_max_miles and rel_dist_miles > filtering_distance_min_miles:
                aircraft.update({'isGPS':'false'})
                aircraft.update({'hex':icao_to_n(aircraft['hex'])})
                j_aircraft = json.dumps(aircraft)
                send_json(j_aircraft)
    f.close()

#Controls aircraft JSON parsing thread
def run_aircraft_thread():
    while True:
        #if XR client has disconnected, lock loop until client reconnects
        while clientLock: pass
        opentime = datetime.now()
        send_aircraft_data("../dump1090/jsondata/aircraft.json")
        #delay next read for a second (takes into account time it took for last read: 1.0 - time to read last)
        if (datetime.now() - opentime).total_seconds() < 1.0:
            time.sleep(1.0 - (datetime.now() - opentime).total_seconds())

#Handle shutdown on CTRL+C
def signal_handler(sig, frame):
    print('SIGINT handled, closing sockets')
    client.close()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

if __name__ == '__main__':
    #Connect to server socket
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except socket.error:
        print('Failed to create socket')
        sys.exit()
    client.connect((ip, port))
    print('Socket Connected to ' + ip)
    client.recv(1) # wait for server confirmation to begin sending data

    #Start listener thread
    listener = threading.Thread(target = listen)
    listener.start()

    #Start GPS Poller thread
    gpsPoller = threading.Thread(target = run_gps_thread)
    gpsPoller.start()

    run_aircraft_thread()
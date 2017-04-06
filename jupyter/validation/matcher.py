import os
import sys
import json
import redis
import time
import multiprocessing
import threading
from Queue import Queue
import valhalla
import pickle
from glob import glob
from sets import Set
import operator
import csv
import calendar

from IPython.display import clear_output

# constants for
MAX_ELAPSED_TIME = 0    # 5 mins (in milliseconds)
MAX_DISTANCE = 0                 # 5km (in meters)

DEBUG_OUTPUT = True                 # enables complete data on traces and match stats

# haversine implementation from:
# https://raw.githubusercontent.com/mapado/haversine/

from math import radians, cos, sin, asin, sqrt
AVG_EARTH_RADIUS = 6371  # in km

def haversine(point1, point2):
    """ Calculate the great-circle distance between two points on the Earth surface.

    :input: two Point objects, containing the latitude and longitude of each point
    in decimal degrees.

    Example: haversine(point1, point2)

    :output: Returns the distance bewteen the two points in meters
    """

    # convert all latitudes/longitudes from decimal degrees to radians
    lat1, lng1, lat2, lng2 = map(radians, (point1.lat, point1.lon, point2.lat,  point2.lon))

    # calculate haversine
    lat = lat2 - lat1
    lng = lng2 - lng1

    d = sin(lat * 0.5) ** 2 + cos(lat1) * cos(lat2) * sin(lng * 0.5) ** 2
    h = 2 * AVG_EARTH_RADIUS * asin(sqrt(d))

    return h * 1000 # in meters


class Point:
    def __init__(self, lat, lon, time):
        self.lat = lat
        self.lon = lon
        self.time = time


class Trace:
    def __init__(self, vehicle_id):
        self.vehicle_id = vehicle_id
        self.lock = threading.Lock()
        self.points = []
        self.total_time = 0
        self.total_distance = 0
        self.last_time = 0

    def acquire(self):
        return self.lock.acquire(False)

    def release(self):
        return self.local.release()

    def flush(self):
        return self.__process_trace()

    def add_point(self, point):

        match = False
        # appened points to list and update stats
        if len(self.points) > 0:
            # if in time order append
            if self.points[-1].time < point.time:

                # calc deltas from last point and add to total
                self.total_time += point.time - self.points[-1].time
                self.total_distance += self.points[-1].distance(point)

                self.points.append(point)

            # not in order, insert in correct place
            else:

                # loop over list once and insert -- faster than sorting
                for i in range(0, len(self.points)):
                    if self.points[i].time > point.time:
                        self.points.insert(point, i)
                        break;

                self.__recalc()

            # if more than max time or distance has been accured process the trace
            if self.total_time > MAX_ELAPSED_TIME or self.total_distance > MAX_DISTANCE:
                match = self.__process_trace()

        # if first point just add to the list
        else:
            # can start a trace before last_time
            if point.time > self.last_time:
                self.points.append(point)
            else:
                pass # TODO report on old/out of order trace points


        # if this is the latest time we've seen update last_time (future traces can't before this)
        if point.time > self.last_time:
            # save the
            self.last_time = point.time

        return match


    # recalculate stats for points
    def __recalc(self):
        # need to reset stats since we're inserting
        self.total_time = 0
        self.total_distance = 0

        # loop over list and recalulate time and distnace
        for i in range(0, len(self.points)):
            if i > 0:
                self.total_time += self.points[i].time - self.points[i-1].time
                self.total_distance += self.points[i].distance(self.points[i-1])


    # trace parsing code borrowed and heavily modified from opentraffic/reporter (and modified for validation)
    def __process_trace(self):

        if len(self.points) < 2:
            return False

        # build json object to send to valhalla matcher
        trace = {'uuid': self.vehicle_id, 'trace': self.points}

        print trace

        result = self.thread_local.segment_matcher.Match(json.dumps(trace, separators=(',', ':')))

        segments = json.loads(result)

        # if debug keep complete trace for testing purposes
        if DEBUG:
             segments['trace'] = self.points

        #if there are segments
        if len(segments['segments']):
            #if the last one had the beginning of the ots but not the end we'll want to continue it
            if segments['segments'][-1]['start_time'] >= 0 and segments['segments'][-1]['end_time'] < 0:
                #gets the begin index of the last partial
                begin_index = segments['segments'][-1]['begin_shape_index']
                # keep the points after the last partial
                self.points = self.points[begin_index:]

            else:
                # done
                self.points = []

            self.__recalc()

            #if any others are partial, we do not need so remove them -- don't throw out data in debug mode
            if not DEBUG:
                segments['segments'] = [ seg for seg in segments['segments'] if seg['length'] > 0 ]


        return {'uuid':uuid, 'segments':segments}


class Matcher:
    def __init__(self, tile_dir, outfile):

        self.tile_dir = tile_dir
        if not os.path.exists(self.tile_dir ):
            print "Data directory " + self.tile_dir  + " does not exist."
            sys.exit()

        #get the config and setup for it
        print "Building tile config..."

        config_file = "/tmp/valhalla_config.json"

        os.system("valhalla_build_config --mjolnir-tile-dir " + self.tile_dir  + "valhalla_tiles --mjolnir-tile-extract " + self.tile_dir  + "tiles.tar --mjolnir-timezone " + self.tile_dir  + "valhalla_tiles/timezones.sqlite --mjolnir-admin " + self.tile_dir  + "valhalla_tiles/admins.sqlite > " + config_file)

        try:
            with open(config_file) as f:
                conf = json.load(f)
                valhalla.Configure(config_file)
                os.environ['REDIS_HOST']

        except Exception as e:
            sys.stderr.write('Problem with config file: {0}\n'.format(e))
            sys.exit(1)


        self.point_queue = Queue(maxsize=1000)
        self.processed_queue = Queue(maxsize=1000)
        self.vehicles = {}

        self.thread_local = threading.local()

        # setup output thread
        self.outfile = open(outfile, 'w')

        t_output = threading.Thread(target = self.__output_matches)
        t_output.setDaemon(1)
        t_output.start()

        # setup the processor
        pool_size = int(os.environ.get('THREAD_POOL_MULTIPLIER', 1)) * multiprocessing.cpu_count()
        print "Starting validation processor with " + str(pool_size) + " threads"
        for x in range(pool_size):
            t = threading.Thread(target = self.__start_thread)
            t.setDaemon(1)
            t.start()

    def process_file(self, csv_file):

        print "Processing " + csv_file + "..."

        point_count = 0
        failed_count = 0
        start_time = time.time()

        # Read the file
        with open(csv_file, 'r') as csvfile:
            columns = ("time","uuid","lat","lon")
            reader = csv.DictReader(csvfile, fieldnames=columns)

            for row in reader:

                point_count += 1
                # create point object
                vehicle_id = row['uuid']
                # Convert to epoch seconds
                t = calendar.timegm(time.strptime(row.get(columns[0]),"%Y-%m-%d %H:%M:%S"))
                # These shouldn't be strings
                lon = float(row['lon'])
                lat = float(row['lat'])

                point = {'point': Point(lat, lon, t), 'vehicle_id': vehicle_id}

                # loader thread creates vehicle trace objects to prevent a race condition.
                # probably some smart locking solution that would do this in the worker threads...
                # TODO clean up thread for unused vehicles
                if not vehicle_id in self.vehicles:
                    self.vehicles[vehicle_id] = Trace(vehicle_id)



                # push point into processing queue
                self.point_queue.put(point)

        self.point_queue.join()

        # flsuh any unprocessed points in traces
        for vehicle_id in self.vehicles:
            match = self.vehicles[vehicle_id].flush()
            if match:
                # push match into queue for seralization
                self.processed_queue.put(match)

        self.processed_queue.join()

        end_time = time.time()
        total_time = end_time - start_time
        seconds_per_point = point_count / total_time

        print "Total: " + str(total_time) + " seconds for " + str(point_count) + " points (" + str(seconds_per_point) + " pt/sec)"

    def process_trace(self, json_trace):
        pass
        #trace = json.loads(json_trace)
        #self.trace_queue.put(trace)
        #return self.processed_queue.get()

    def __output_matches(self):

        # drain processed queue
        while True:
            try:
                processed = self.processed_queue.get()
                json.dump(processed, self.outfile)

            except Exception as e:
                pass # TODO handle serliziation errors

            finally:
                self.processed_queue.task_done()

    def __start_thread(self):

        setattr(self.thread_local, 'segment_matcher', valhalla.SegmentMatcher())
        setattr(self.thread_local, 'cache', redis.Redis(host=os.environ['REDIS_HOST']))

        # process traces
        while True:
            try:
                point = self.point_queue.get()
                vehicle = self.vehicles[point.vehicle_id]

                # only one thread can have a lock on a vehicle at a time
                # TODO improve this so threads don't split/fight over blocks vehicle_ids in the queue
                if vehicle.acquire():
                    try:
                        match = vehicle.add_point(point)
                        if match:
                            # push match into queue for seralization
                            self.processed_queue.put(match)

                    except Exception as e:
                        pass

                    # are we sure vehicle locks will always be released? multithreading!
                    vehicle.release()

                    # only call this if we got a lock -- otherwise saving point for another thread with the vehicle lock
                    self.point_queue.task_done()

            except Exception as e:
                # failed on point for unknow reasons -- bailing on point -- TODO log failures
                self.point_queue.task_done()
                pass


class Collector:
    def __init__(self, tile_dir):

        # keep tile dir for later so we can collect geometries for matching traces
        self.tile_dir = tile_dir

        self.traces = {}
        self.matches = {}

    def add_trace(self, trace):
        uuid = str(trace.get('uuid'))
        if not uuid in self.traces:
            self.traces[uuid] = []

        self.traces[uuid].append(trace)

    def add_match(self, uuid, match):

        if match == None:
            return

        if not uuid in self.matches:
            self.matches[uuid] = []

        self.matches[uuid].append(match)

    def generate_results(self, resutls_file):

        self.__load_osmlr_segments()



    def __load_osmlr_segments(self):

        self.osmlr_segments = []

         # collect matched segment geometries from OSMLR tiles to include in output file
        matched_osml_segments = Set()

        # get list of segment ids in matched traces
        for uuid in self.matches:
            for match in self.matches[uuid]:
                for segment in match['segments']:
                    matched_osml_segments.add(str(segment["segment_id"]))


        if len(matched_osml_segments) > 0:
            # walk osmlr tile directory to get list of available tiles
            tile_files = [y for x in os.walk(self.tile_dir + 'osmlr_geotiles/') for y in glob(os.path.join(x[0], '*.json'))]

            # iterate through
            for tile in tile_files:
                with open(tile) as json_data:
                    tile_data = json.load(json_data)
                    for feature in tile_data['features']:
                        if str(feature['properties']['osmlr_id']) in matched_osml_segments:
                            self.osmlr_segments.append(feature)

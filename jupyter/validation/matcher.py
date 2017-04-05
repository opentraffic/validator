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

from IPython.display import clear_output

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
        
        with open(resutls_file, 'w') as outfile:
            json.dump({'traces':self.traces,'matches':self.matches,'osmlr_segments':self.osmlr_segments}, outfile)
       
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

class Matcher:
    def __init__(self, tile_dir):
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
    
        self.trace_queue = Queue(maxsize=1000)
        self.processed_queue = Queue(maxsize=1000)
        self.thread_local = threading.local()
        
            
        # setup the processor
        pool_size = int(os.environ.get('THREAD_POOL_MULTIPLIER', 1)) * multiprocessing.cpu_count()
        print "Starting validation processor with " + str(pool_size) + " threads"
        for x in range(pool_size):
            t = threading.Thread(target = self.__start_thread)
            t.setDaemon(1)
            t.start()
            
    def process_file(self, json_file):
        
        print "Processing " + json_file + "..."
        
        collector = Collector(self.tile_dir)
        
        count = 0
        failed_count = 0
        start_time = time.time()
        with open(json_file) as json_data:
            for json_trace in json_data:
                count += 1
                try:
                    
                    trace = json.loads(json_trace)

                    collector.add_trace(trace)

                    self.trace_queue.put(trace)

                    while not self.processed_queue.empty():
                        matched_trace = self.processed_queue.get(block=False)
                        if matched_trace:
                            collector.add_match(matched_trace['uuid'], matched_trace['match'])
                except:
                    failed_count += 1
                    
                if count % 10 == 0:
                    clear_output()
                    print "Processing " + json_file + "..." + str(count) + " processed (" + str(failed_count) + " failed)"
                        
        
        print "Finishing processing..."
        # wait until processsed 
        self.trace_queue.join()
        
        print "Trace queue empty..."
        while not self.processed_queue.empty():
            matched_trace = self.processed_queue.get(block=False)
            if matched_trace:
                collector.add_match(matched_trace['uuid'], matched_trace['match'])
            
        end_time = time.time()
        
        total_time = end_time - start_time
       
        seconds_per_trace = total_time / count
        
        print "Total: " + str(total_time) + " seconds for " + str(count) + " traces (" + str(seconds_per_trace) + ")"
        
        return collector
    
    def process_trace(self, json_trace):
        trace = json.loads(json_trace)
        self.trace_queue.put(trace)
        return self.processed_queue.get()    
            
    def __start_thread(self):
        
        setattr(self.thread_local, 'segment_matcher', valhalla.SegmentMatcher())
        setattr(self.thread_local, 'cache', redis.Redis(host=os.environ['REDIS_HOST']))
        
        # process traces
        while True:
            trace = self.trace_queue.get()
            
            try:
                processed = self.__process_trace(trace)
                
                # can't keep everything in memory TODO stream processed data out to file
                self.processed_queue.put(processed)
            except Exception as e:
                #sys.stderr.write('Problem with trace: {0}\n'.format(e))
                pass
            
            self.trace_queue.task_done()
        

    # trace parsing code borrowed from opentraffic/reporter (and modified for validation)
    # would be better to use reporter code as a library to ensure consistency/debugging support
    # but reporter need to be modified to support use outside of reporter->datastore workflow
    
    def __process_trace(self, trace):
        #lets get the uuid from json the request
        uuid = trace.get('uuid')
        if uuid is not None:
            #do we already know something about this vehicleId already? Let's check Redis
            partial = self.thread_local.cache.get(uuid)
            if partial:
                partial = pickle.loads(partial)
                time_diff = trace['trace'][0]['time'] - partial[-1]['time']
                #check to make sure time is not stale and not in future
                if time_diff < os.environ.get('STALE_TIME', 60) and time_diff >= 0:
                    #Now prepend the last bit of shape from the partial_end segment that's already in Redis
                    #to the rest of the partial_start segment once it is returned from the segment_matcher
                    trace['trace'] = partial + trace['trace']
        else:
              return #No uuid in segment_match request!

        #ask valhalla to give back OSMLR segments along this trace
        result = self.thread_local.segment_matcher.Match(json.dumps(trace, separators=(',', ':')))
        segments = json.loads(result)

        #if there are segments
        if len(segments['segments']):
            #if the last one had the beginning of the ots but not the end we'll want to continue it
            if segments['segments'][-1]['start_time'] >= 0 and segments['segments'][-1]['end_time'] < 0:
                #gets the begin index of the last partial
                begin_index = segments['segments'][-1]['begin_shape_index']
                #in Redis, set the uuid as key and trace from the begin index to the end
                self.thread_local.cache.set(uuid, pickle.dumps(trace['trace'][begin_index:]), ex=os.environ.get('PARTIAL_EXPIRY', 300))
            #if any others are partial, we do not need so remove them
            segments['segments'] = [ seg for seg in segments['segments'] if seg['length'] > 0 ]
            segments['mode'] = "auto"
            segments['provider'] = "GRAB" #os.enviorn['PROVIDER_ID']
            #segments['reporter_id'] = os.environ['REPORTER_ID']
        
        matched_trace = None
        #Now we will send the whole segments on to the datastore
        if len(segments['segments']):
            matched_trace = segments

    
        return {'uuid':uuid, 'match':matched_trace}


        
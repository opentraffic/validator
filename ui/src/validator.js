import L from 'leaflet';
import turf from 'turf';
import { sortBy, map, reduce } from 'underscore';


class TraceStats {

  constructor(points) {
    this.length = points.length ;

    if(this.length > 0) {

      // don't assume points are in order
      var sorted_points = sortBy(points, 'time');

      this.points = sorted_points;

      this.start_time = this.points[0].time;

      // keep track of distances and times between each point
      this.distances = [];
      this.times = [];

      var previous_point = this.points[0];

      // itterate of points and calc distance and time intervals
      for(var pos in this.points) {

        var next_point = this.points[pos];
        this.end_time = this.points[pos].time;

        if(pos > 0) {
            var distance = Math.round(turf.distance(this.getPointFeature(previous_point), this.getPointFeature(next_point)) * 1000);
            var time = next_point - previous_point;

            this.distances.push(distance);
            this.times.push(time);
        }
      }
    }
  }

  getAverageSpeed() {
    return reduce(this.distances, function(memo, num){ return memo + num; }, 0) / reduce(this.times, function(memo, num){ return memo + num; }, 0);
  }

  getPointCoords(point) {
    return [point.lon, point.lat]
  }

  getPointFeature(point) {
    return turf.point(this.getPointCoords(point))
  }

  getLinestring() {
      var mapped_points = map(this.points, this.getPointCoords);
      return turf.lineString(mapped_points)
  }

  getMultipoint() {
      var mapped_points = map(this.points, this.getPointCoords);
      return turf.multiPoint(mapped_points)
  }

  isEmpty() {
    return this.length > 0 ? false : true;
  }
}

class VehicleStats {

  // takes an id and an array of arrays of points
  constructor(vehicle_id, grouped_points) {

    this.vehicle_id = vehicle_id;
    this.traces = []

    // compute stats for all groups of points and store
    for(var pos in grouped_points) {
      var traceStats = new TraceStats(grouped_points[pos].trace);
      if(!traceStats.isEmpty())
        this.traces.push(traceStats);
    }

  }

  getLayer() {

    var layer = L.featureGroup();

    var markerStyle = {
      radius: 8,
      fillColor: "#57b2a4",
      color: "#57b2a4",
      weight: 1,
      opacity: 0.5,
      fillOpacity: 0.5
    };

    var pathStyle = {
      color: "#444444",
      weight: 10,
      opacity: 0.50
    };

    for(var pos in this.traces) {
      var trace = this.traces[pos];
      L.geoJSON(trace.getLinestring()).setStyle(pathStyle).addTo(layer);

      L.geoJSON(trace.getMultipoint(), {
          pointToLayer: function (feature, latlng) {
              return L.circleMarker(latlng, markerStyle);
          }}).addTo(layer);
    }

    return layer;

  }
}


export default class Validator {

  constructor(data) {

    // load  data
    this.trace_count = Object.keys(data.traces).length
    this.match_count = Object.keys(data.matches).length
    this.traces = data.traces;
    this.matches = data.matches;

    // populate geometry hash
    this.geometries = {};

    for(var osmrlr_segment in data.osmlr_segments){
      this.geometries[data.osmlr_segments[osmrlr_segment].properties.osmlr_id] = data.osmlr_segments[osmrlr_segment].geometry;
    }

    // calcualte statistics for traces
    this.vehicle_stats = {}

    for(var vehicle_id in this.traces) {
        this.vehicle_stats[vehicle_id] = new VehicleStats(vehicle_id, this.traces[vehicle_id]);
    }
  }

  getVehicleLayer(vehicle_id) {

    var stats = this.vehicle_stats[vehicle_id]

    return stats.getLayer();
  }

  getMatchLayer(vehicle_id) {

    var stats = this.vehicle_stats[vehicle_id]

    return stats.getLayer();
  }

  getVehicleStats() {
    return this.vehicle_stats;
  }

  getMatches() {
    return this.matchess;
  }

  getMatchCount() {
    return this.match_count;
  }

}

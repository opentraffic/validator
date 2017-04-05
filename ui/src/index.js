
require('uikit/src/less/uikit.theme.less');
require("leaflet_css");

import $ from 'jquery';
// export for others scripts to use
window.$ = $;

import UIkit from 'uikit';
import Icons from 'uikit/dist/js/uikit-icons';

import L from 'leaflet';

import Validator from './validator';

// loads the Icon plugin
UIkit.use(Icons);


// init page

$('#select_trace_form').hide();

// initialize the map
var leafletMap = L.map('mapContainer').setView([51.505, -0.09], 13);


L.tileLayer('http://{s}.tile.osm.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="http://osm.org/copyright">OpenStreetMap</a> contributors'
}).addTo(leafletMap);

var layerGroup = L.layerGroup().addTo(leafletMap);

// init test data and valiator object
var request = $.ajax('test-data.json', {dataType:'json'});
var validator;

// load the validator object and populate select droppdown
request.done(function( response ) {
  validator = new Validator(response);

  $('#match_count').html(validator.getMatchCount() + " matches");

  var vehicle_stats = validator.getVehicleStats()
  for(var vehicle_id in vehicle_stats){
    $('#select_trace').append('<option value="' + vehicle_id + '">' + vehicle_id + '</option>')
  }

  $('#select_trace_form').show();
});

// render map overlay on select trace
$('#select_trace').change(function(){

  var vehicle_id = $('#select_trace option:selected').val();

  layerGroup.clearLayers();
  var vehicleLayer = validator.getVehicleLayer(vehicle_id);
  var bounds = vehicleLayer.getBounds();
  vehicleLayer.addTo(layerGroup);

  var matchLayer = validator.getMatchLayer(vehicle_id);
  matchLayer.addTo(layerGroup);

  leafletMap.fitBounds(bounds);

});

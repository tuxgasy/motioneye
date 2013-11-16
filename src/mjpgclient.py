
# Copyright (c) 2013 Calin Crisan
# This file is part of motionEye.
#
# motionEye is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>. 

import datetime
import logging
import re
import socket

from tornado import iostream, ioloop

import config
import motionctl
import settings


class MjpgClient(iostream.IOStream):
    clients = {} # dictionary of clients indexed by camera id
    last_jpgs = {} # dictionary of jpg contents indexed by camera id
    last_access = {} # dictionary of access moments indexed by camera id
    
    def __init__(self, camera_id, port):
        self._camera_id = camera_id
        self._port = port
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        iostream.IOStream.__init__(self, s)
        
    def connect(self):
        iostream.IOStream.connect(self, ('localhost', self._port), self._on_connect)
        MjpgClient.clients[self._camera_id] = self
        
        logging.debug('mjpg client connecting on port %(port)s...' % {'port': self._port})
    
    def close(self):
        try:
            del MjpgClient.clients[self._camera_id]
            
            logging.debug('mjpg client for camera %(camera_id)s removed' % {'camera_id': self._camera_id})
            
        except KeyError:
            pass
        
        iostream.IOStream.close(self)
    
    def _check_error(self):
        if self.error is None:
            return False
        
        self._error(self.error)
        
        return True
     
    def _error(self, error):
        logging.error('mjpg client error: %(msg)s' % {
                'msg': unicode(error)})
        
        try:
            self.close()
        
        except:
            pass
    
    def _on_connect(self):
        logging.debug('mjpg client connected on port %(port)s...' % {'port': self._port})
        
        self.write(b"GET / HTTP/1.0\r\n\r\n")
        self._seek_content_length()
        
    def _seek_content_length(self):
        if self._check_error():
            return
        
        self.read_until('Content-Length:', self._on_before_content_length)
    
    def _on_before_content_length(self, data):
        if self._check_error():
            return
        
        self.read_until('\r\n\r\n', self._on_content_length)
    
    def _on_content_length(self, data):
        if self._check_error():
            return
        
        matches = re.findall('(\d+)', data)
        if not matches:
            self._error('could not find content length in mjpg header line "%(header)s"' % {
                    'header': data})
            
            return
        
        length = int(matches[0])
        
        self.read_bytes(length, self._on_jpg)
    
    def _on_jpg(self, data):
        MjpgClient.last_jpgs[self._camera_id] = data
        self._seek_content_length()


def _garbage_collector():
    logging.debug('running garbage collector for mjpg clients...')
    
    now = datetime.datetime.utcnow()
    for client in MjpgClient.clients.values():
        camera_id = client._camera_id
        last_access = MjpgClient.last_access.get(camera_id)
        if last_access is None:
            continue
        
        delta = now - last_access
        delta = delta.days * 86400 + delta.seconds
        
        if delta > settings.MJPG_CLIENT_TIMEOUT:
            logging.debug('mjpg client for camera %(camera_id)s timed out' % {'camera_id': camera_id})
            client.close()

    io_loop = ioloop.IOLoop.instance()
    io_loop.add_timeout(datetime.timedelta(seconds=settings.MJPG_CLIENT_TIMEOUT), _garbage_collector)


def get_jpg(camera_id):
    if not motionctl.running():
        return None
    
    if camera_id not in MjpgClient.clients:
        # mjpg client not started yet for this camera
        
        logging.debug('creating mjpg client for camera id %(camera_id)s' % {
                'camera_id': camera_id})
        
        camera_config = config.get_camera(camera_id)
        if not camera_config['@enabled'] or camera_config['@proto'] != 'v4l2':
            logging.error('could not start mjpg client for camera id %(camera_id)s: not enabled or not local' % {
                    'camera_id': camera_id})
            
            return None
        
        port = camera_config['webcam_port']
        client = MjpgClient(camera_id, port)
        client.connect()

    MjpgClient.last_access[camera_id] = datetime.datetime.utcnow()
    
    return MjpgClient.last_jpgs.get(camera_id)


def close_all():
    for client in MjpgClient.clients.values():
        client.close()


# run the garbage collector for the first time;
# this will start the timeout mechanism
_garbage_collector()
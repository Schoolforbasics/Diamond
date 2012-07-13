"""
Processes OpenVPN metrics. This collector can process multiple OpenVPN
instances (even from a server box). In addition to the path, you may
also specify a name of the instance.

You can use both the status file or the tcp management connection to
retrieve the metrics.

To parse the status file::

    instances = file:///var/log/openvpn/status.log

Or, to override the name (now "status"):

    instances = file:///var/log/openvpn/status.log?developers

To use the management connection::

    instances = tcp://127.0.0.1:1195

Or, to override the name (now "127_0_0_1"):

    instances = tcp://127.0.0.1:1195?developers

You can also specify multiple and mixed instances::

    instances = file:///var/log/openvpn/developers.log, tcp://10.0.0.1:1195?admins
    
#### Dependencies

 * urlparse

"""
    
from diamond.metric import Metric
import diamond.collector
import os.path
import urlparse

class OpenVPNCollector(diamond.collector.Collector):

    def get_default_config_help(self):
        config_help = super(OpenVPNCollector, self).get_default_config_help()
        config_help.update({
        })
        return config_help

    def get_default_config(self):
        """
        Returns the default collector settings
        """
        config = super(OpenVPNCollector, self).get_default_config()
        config.update(  {
            'path':      'openvpn',
            'method':    'Threaded',
            'instances': 'file:///var/log/openvpn/status.log',
            'timeout':   '10',
        } )
        return config

    def collect(self):
        if isinstance(self.config['instances'], basestring):
            instances = [self.config['instances']]
        else:
            instances = self.config['instances']

        for uri in instances:
            parsed = urlparse.urlparse(uri)
            collect = getattr(self, 'collect_%s' % (parsed.scheme,), None)
            if collect:
                collect(uri)
            else:
                self.log.error('OpenVPN no handler for %s' % (uri,))

    def collect_file(self, uri):
        parsed = urlparse.urlparse(uri)
        filename = parsed.path
        if '?' in filename:
            filename, name = filename.split('?')
        else:
            name = os.path.splitext(os.path.basename(filename))[0]

        if not os.access(filename, os.R_OK):
            self.log.error('OpenVPN collect failed: unable to read "%s"' % (filename,))
            return
        else:
            self.log.info('OpenVPN parsing "%s" file: %s' % (name, filename))

        fd = open(filename, 'r')
        lines = fd.readlines()
        fd.close()

        self.parse(name, lines)

    def collect_tcp(self, uri):
        parsed = urlparse.urlparse(uri)
        try:
            host, port = parsed.netloc.split(':')
            port = int(port)
        except ValueError:
            self.log.error('OpenVPN expected host:port in URI, got "%s"' % (parsed.netloc,))
            return

        if '?' in parsed.path:
            name = parsed.path[1:]
        else:
            name = host.replace('.', '_')
        
        self.log.info('OpenVPN parsing "%s" tcp: %s:%d' % (name, host, port))

        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.settimeout(int(self.config['timeout']))
            server.connect((host, port))

            fd = server.makefile('rb')
            line = fd.readline()
            if not line.startswith('>INFO:OpenVPN'):
                self.log.debug('OpenVPN received: %s' % (line.rstrip(),))
                self.log.error('OpenVPN protocol error')
                server.close()
                return

            server.send('status\r\n')

            lines = []
            while True:
                line = fd.readline()
                lines.append(line)
                if line.strip() == 'END':
                    break

            # Hand over data to the parser
            self.parse(name, lines)

            # Bye
            server.close()

        except socket.error, e:
            self.log.error('OpenVPN management connection error: %s' % (str(e),))
            return

    def parse(self, name, lines):
        for line in lines:
            self.log.debug('OpenVPN: %s' % (line.rstrip(),))

        import time
        time.sleep(0.5)

        section = ''
        heading = []
        for line in lines:
            if line.strip() == 'END':
                break
            elif line.lower().startswith('openvpn statistics'):
                section = 'statistics'
            elif line.lower().startswith('openvpn client list'):
                section = 'clients'
            elif line.lower().startswith('routing table'):
                # ignored
                section = ''
            elif line.lower().startswith('global stats'):
                section = 'global'
            elif ',' in line:
                key, value = line.split(',', 1)
                if key.lower() == 'updated':
                    continue

                if section == 'statistics':
                    # All values here are numeric
                    self.publish_number('.'.join([
                            name,
                            'global',
                            key,
                        ]), value)

                elif section == 'clients':
                    # Clients come with a heading
                    if not heading:
                        heading = line.strip().split(',')
                    else:
                        info = {}
                        for k, v in zip(heading, line.strip().split(',')):
                            info[k.lower()] = v

                        self.publish_number('.'.join([
                                name,
                                section,
                                info['common name'].replace('.', '_'),
                                'bytes_rx'
                            ]), info['bytes received'])
                        self.publish_number('.'.join([
                                name,
                                section,
                                info['common name'].replace('.', '_'),
                                'bytes_tx'
                            ]), info['bytes sent'])

                elif section == 'global':
                    # All values here are numeric
                    self.publish_number('.'.join([
                            name,
                            section,
                            key,
                        ]), value)
                    
            elif line.startswith('END'):
                break


    def publish_number(self, key, value):
        key = key.replace('/', '-').replace(' ', '_').lower()
        try:
            value = long(value)
        except ValueError:
            self.log.error('OpenVPN expected a number for "%s", got "%s"' % (key, value))
            return
        else:
            self.publish(key, value)


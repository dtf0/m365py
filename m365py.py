from typing import Callable, Iterable, Optional
from collections import namedtuple

import struct
import time
import json
import logging
import M365Message

from bluepy.btle import Peripheral, Characteristic, UUID, DefaultDelegate, ADDR_TYPE_RANDOM

stream_handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
stream_handler.setFormatter(formatter)

log = logging.getLogger('m365py')
log.setLevel(logging.DEBUG)
log.addHandler(stream_handler)

def phex(s):
    return ''.join('/x{:02x}'.format(x) for x in s)

class M365Delegate(DefaultDelegate):
    def __init__(self, m365):
        DefaultDelegate.__init__(self)
        self._m365 = m365
        self.disjointed_messages = []

        self.motor_info_first_part = None
        self.general_info_first_part = None

    @staticmethod
    def unpack_to_dict(fields, unpacked_tuple):
        result = namedtuple('namedtuple', fields)
        result = result._make(unpacked_tuple) # insert unpacked values
        result = result._asdict()             # convert to OrderedDict
        result = dict(result)                 # convert to regular dict
        return result

    def handle_message(self, message):
        log.debug("Received message: {}".format(message.as_dict()))
        log.debug("Payload: {}".format(phex(message._payload)))

        result = {}
        if message._attribute == M365Message.Attribute.DISTANCE_LEFT:
            result = M365Delegate.unpack_to_dict(
                'distance_left_km',
                struct.unpack('<H', message._payload)
            )

            result['distance_left_km'] /= 100  # km

        elif message._attribute == M365Message.Attribute.SPEED:
            result = M365Delegate.unpack_to_dict(
                'speed_kmh',
                struct.unpack('<H', message._payload)
            )

            result['speed_kmh'] /= 100

        elif message._attribute == M365Message.Attribute.DISTANCE_SINCE_STARTUP:
            result = M365Delegate.unpack_to_dict(
                'distance_since_startup_km',
                struct.unpack('<H', message._payload[6:8])
            )

            result['distance_since_startup_km'] /= 1000

        elif message._attribute == M365Message.Attribute.TAIL_LIGHT:
            is_light_on = message._payload[0] == 0x02
            result = {'is_tail_light_on': is_light_on}

        elif message._attribute == M365Message.Attribute.CRUISE:
            is_cruise_on = message._payload[0] == 0x01
            result = {'is_cruise_on': is_cruise_on}

        elif message._attribute == M365Message.Attribute.BATTERY_INFO:
            result = M365Delegate.unpack_to_dict(
                'battery_capacity battery_percent battery_current battery_voltage battery_temperature_1 battery_temperature_2',
                struct.unpack('<HHhHBB', message._payload)
            )

            result['battery_capacity']      /= 1000 # Ah
            result['battery_current']       /= 100  # A
            result['battery_voltage']       /= 100  # V
            result['battery_temperature_1'] -= 20   # C
            result['battery_temperature_2'] -= 20   # C

        elif message._attribute == M365Message.Attribute.BATTERY_VOLTAGE:
            result = M365Delegate.unpack_to_dict(
                'battery_voltage',
                struct.unpack('<H', message._payload)
            )

            result['battery_voltage'] /= 100 # V

        elif message._attribute == M365Message.Attribute.BATTERY_CURRENT:
            result = M365Delegate.unpack_to_dict(
                'battery_current',
                struct.unpack('<h', message._payload)
            )

            result['battery_current'] /= 100 # A

        elif message._attribute == M365Message.Attribute.BATTERY_PERCENT:
            result = M365Delegate.unpack_to_dict(
                'battery_percent',
                struct.unpack('<H', message._payload)
            )

        elif message._attribute == M365Message.Attribute.GENERAL_INFO:
            #          [                      SERIAL                          ][          PIN         ][ VER  ]
            # payload: /x31/x36/x31/x33/x32/x2f/x30/x30/x30/x39/x35/x32/x39/x32/x30/x30/x30/x30/x30/x30/x38/x01
            result = M365Delegate.unpack_to_dict(
                'serial pin version',
                struct.unpack('<14s6sH', message._payload)
            )
            result['serial']  = str(result['serial'])
            result['pin']     = str(result['pin'])
            result['version'] = '{:02x}'.format(result['version'])
            result['version'] = 'V' + result['version'][0] + '.' + result['version'][1] + '.' + result['version'][2]

        elif message._attribute == M365Message.Attribute.MOTOR_INFO:
            result = M365Delegate.unpack_to_dict(
                'error warning flags workmode battery_percent speed_kmh speed_average_kmh odometer_km trip_distance_m uptime_s frame_temperature',
                struct.unpack('<HHHHHHHIhhhxxxxxxxx', message._payload)
            )

            result['speed_kmh']         /= 100  # km /h
            result['speed_average_kmh'] /= 100  # km /h
            result['odometer_km']       /= 1000 # km
            result['frame_temperature'] /= 10   # °C

        elif message._attribute == M365Message.Attribute.TRIP_INFO:
            #          [uptime][]
            # payload: xec/x00 /x00/x00/x00/x00/x00/x00/xe6/x00
            result = M365Delegate.unpack_to_dict(
                'uptime_s trip_distance_m frame_temperature',
                struct.unpack('<HIxxh', message._payload)
            )

            result['frame_temperature'] /= 10   # °C

        elif message._attribute == M365Message.Attribute.BATTERY_CELL_VOLTAGES:
            #          [cell1 ][cell2 ]                     ...                                [cell10][           ???            ]
            # payload: /x2d/x10/x2e/x10/x1d/x10/x2f/x10/x34/x10/x34/x10/x3a/x10/x3a/x10/x2e/x10/x2f/x10/x00/x00/x00/x00/x00/x00/x00
            result = M365Delegate.unpack_to_dict(
                '''cell_1_voltage cell_2_voltage cell_3_voltage cell_4_voltage
                cell_5_voltage cell_6_voltage cell_7_voltage cell_8_voltage
                cell_9_voltage cell_10_voltage''',
                struct.unpack('<HHHHHHHHHHxxxxxxx', message._payload)
            )

            for key, value in result.items():
                result[key] = value / 100 # V

        else:
            log.warning('Unhandled message!')
            return

        # write result to m365 cached state
        for key, value in result.items():
            # hacky way of writing key and value to state
            self._m365.state.__dict__[key] = value

        # call user callback
        if self._m365._callback:
            self._m365._callback(self._m365, message, result)



    def handleNotification(self, cHandle, data):
        data = bytes(data)

        # sometimes we receive empty payload, ignore these
        if len(data) == 0: return
        parse_status, message = M365Message.Message.parse_from_bytes(data)

        if parse_status == M365Message.ParseStatus.OK:
            self.handle_message(message)

        elif parse_status == M365Message.ParseStatus.DISJOINTED:
            self.disjointed_messages.append(data)

        elif parse_status == M365Message.ParseStatus.INVALID_HEADER:
            # This could mean we got rest of disjointed message
            for i, prev_data in enumerate(self.disjointed_messages):
                combined_data = bytearray()
                combined_data.extend(prev_data)
                combined_data.extend(data)
                combined_data = bytes(combined_data)

                # try parse combined data
                parse_status, message = M365Message.Message.parse_from_bytes(combined_data)
                if parse_status == M365Message.ParseStatus.OK:
                    self.handle_message(message)
                    del self.disjointed_messages[i]



class M365State():
    speed_kmh                  = None
    speed_average_kmh          = None
    distance_left_km           = None
    odometer_km                = None
    distance_since_startup_km  = None
    frame_temperature          = None # °C
    is_light_on                = None # bool
    is_in_cruise_mode          = None

    battery_percent        = None # %
    battery_voltage        = None # V
    battery_capacity       = None # Ah
    battery_current        = None # A
    battery_temperature_1  = None # °C
    battery_temperature_2  = None # °C

    def as_dict(self): return self.__dict__

    def to_json(self):
        return json.dumps(self, default=lambda o: o.__dict__, sort_keys=True, indent=4)


class M365(Peripheral):
    RX_CHARACTERISTIC = UUID('6e400003-b5a3-f393-e0a9-e50e24dcca9e')
    TX_CHARACTERISTIC = UUID('6e400002-b5a3-f393-e0a9-e50e24dcca9e')

    def __init__(self, mac_address, callback=None):
        Peripheral.__init__(self)
        self.mac_address = mac_address

        self.state = M365State()
        self._callback = callback

    @staticmethod
    def _find_characteristic(uuid: UUID, chars: Iterable[Characteristic]) -> Optional[Characteristic]:
        results = filter(lambda x: x.uuid == uuid, chars)
        for result in results:  # return the first match
            return result
        return None

    def _try_connect(self):
        log.info('Attempting to indefinitely connect to Scooter: ' + self.mac_address)

        while True:
            try:
                super(M365, self).connect(self.mac_address, addrType=ADDR_TYPE_RANDOM)
                log.info('Successfully connected to Scooter: ' + self.mac_address)

                # Turn on notifications, otherwise there won't be any notification
                self.writeCharacteristic(0xc, b'\x01\x00', True)
                self.writeCharacteristic(0x12, b'\x01\x00', True)

                self._all_characteristics = self.getCharacteristics()
                self._tx_char = M365._find_characteristic(M365.TX_CHARACTERISTIC, self._all_characteristics)
                self._rx_char = M365._find_characteristic(M365.RX_CHARACTERISTIC, self._all_characteristics)

                log.debug('{}, handle: {:x}, properties: {}'.format(self._tx_char, self._tx_char.getHandle(), self._tx_char.propertiesToString()))
                log.debug('{}, handle: {:x}, properties: {}'.format(self._rx_char, self._rx_char.getHandle(), self._rx_char.propertiesToString()))

                break

            except Exception as e:
                log.warning('{}, retrying'.format(e))

    def connect(self):
        self._try_connect()
        self.withDelegate(M365Delegate(self))

    def request(self, message):
        while True:
            try:
                log.debug('Sending message: {}'.format([v for (k,v) in message.__dict__.items()]))
                log.debug('Sending bytes: {}'.format(phex(message._raw_bytes)))
                self._tx_char.write(message._raw_bytes)
                self._rx_char.read()
                break
            except Exception as e:
                log.warning('{}, reconnecting'.format(e))
                self.disconnect()
                self._try_connect()



import traceback
import ast
import copy
import functools
import hashlib
import json
import multiprocessing
import os
import signal
import sys
import threading
import time
import datetime
import argparse
import re

from enum import Enum
from sonic_py_common import daemon_base, device_info, logger
from sonic_py_common import multi_asic
from swsscommon import swsscommon
from portconfig import readJson
from sonic_platform_base.sonic_xcvr.fields import consts

from xcvrd_utilities import sfp_status_helper
from xcvrd_utilities import y_cable_helper
from xcvrd_utilities import port_mapping

#
# Constants ====================================================================
#

SYSLOG_IDENTIFIER = "xcvrd"

PLATFORM_SPECIFIC_MODULE_NAME = "sfputil"
PLATFORM_SPECIFIC_CLASS_NAME = "SfpUtil"

TRANSCEIVER_INFO_TABLE = 'TRANSCEIVER_INFO'
TRANSCEIVER_DOM_SENSOR_TABLE = 'TRANSCEIVER_DOM_SENSOR'
TRANSCEIVER_STATUS_TABLE = 'TRANSCEIVER_STATUS'

# Mgminit time required as per CMIS spec
MGMT_INIT_TIME_DELAY_SECS = 2

# SFP insert event poll duration
SFP_INSERT_EVENT_POLL_PERIOD_MSECS = 1000

# state of read sfp eeprom
READ_EEPROM_STATE_INSERTED = 0
READ_EEPROM_STATE_SECOND_TRY = 1
READ_EEPROM_STATE_LAST_TRY = 2

class insert_event_field(Enum):
    STATE = 0
    TIMESTAMP = 1
    CHECKSUM = 2

DOM_INFO_UPDATE_PERIOD_SECS = 60
STATE_MACHINE_UPDATE_PERIOD_MSECS = 60000
TIME_FOR_SFP_READY_SECS = 1

CERTIFICATED_VENDOR = ['3c-2c-99']
CERTIFICATED_SERIAL = ['M5', 'L1', 'E4', 'J2', 'W1']

# Classify the media connector defined in SFF-8024 and collect the description
# of media connector which are regarded as copper to the list. The pattern below
# are extracted from sff8436InterfaceId/sff8472InterfaceId.
COPPER_CONNECTOR_PATTERN = ['Fibre Channel Style 1 copper connector',
                            'Fibre Channel Style 2 copper connector',
                            'FC Style 1 copper connector',
                            'FC Style 2 copper connector',
                            'BNC/TNC',
                            'CopperPigtail',
                            'Copper pigtail',
                            'RJ45']

# The connector type defined in SFF-8024 and can not distinguish its media type
# directly through the description of media connector. The patterns are extracted
# from sff8436InterfaceId/sff8472InterfaceId.
UNKNOWN_CONNECTOR_PATTERN = ['Unknown or unspecified',
                             'Unknown',
                             'No separable connector']

EVENT_ON_ALL_SFP = '-1'
# events definition
SYSTEM_NOT_READY = 'system_not_ready'
SYSTEM_BECOME_READY = 'system_become_ready'
SYSTEM_FAIL = 'system_fail'
NORMAL_EVENT = 'normal'
# states definition
STATE_INIT = 0
STATE_NORMAL = 1
STATE_EXIT = 2

PHYSICAL_PORT_NOT_EXIST = -1
SFP_EEPROM_NOT_READY = -2

SFPUTIL_LOAD_ERROR = 1
PORT_CONFIG_LOAD_ERROR = 2
NOT_IMPLEMENTED_ERROR = 3
SFP_SYSTEM_ERROR = 4

RETRY_TIMES_FOR_SYSTEM_READY = 24
RETRY_PERIOD_FOR_SYSTEM_READY_MSECS = 5000

RETRY_TIMES_FOR_SYSTEM_FAIL = 24
RETRY_PERIOD_FOR_SYSTEM_FAIL_MSECS = 5000

TEMP_UNIT = 'C'
VOLT_UNIT = 'Volts'
POWER_UNIT = 'dBm'
BIAS_UNIT = 'mA'
GAIN_UNIT = 'dB'

g_dict = {}
platform_name = ''
# Global platform specific sfputil class instance
platform_sfputil = None
# Global chassis object based on new platform api
platform_chassis = None

# Global logger instance for helper functions and classes
# TODO: Refactor so that we only need the logger inherited
# by DaemonXcvrd
helper_logger = logger.Logger(SYSLOG_IDENTIFIER)
helper_logger.set_min_log_priority_info()

#
# Helper functions =============================================================
#

# Get physical port name


def get_physical_port_name(logical_port, physical_port, ganged):
    if ganged:
        return logical_port + ":{} (ganged)".format(physical_port)
    else:
        return logical_port

# Strip units and beautify


def strip_unit_and_beautify(value, unit):
    # Strip unit from raw data
    if type(value) is str:
        width = len(unit)
        if value[-width:] == unit:
            value = value[:-width]
        return value
    else:
        return str(value)
    

def _wrapper_update_sfp_type(physical_port):
    if platform_chassis:
        try:
            platform_chassis.get_sfp(physical_port).update_sfp_type()
        except:
            pass

# Refresh the xcvr_api according to the Identifier field in eeprom when sfp is inserted
def _wrapper_refresh_xcvr_api(physical_port):
    if platform_chassis:
        try:
            sfp = platform_chassis.get_sfp(physical_port)
        except (NotImplementedError, AttributeError):
            return None

        if hasattr(sfp, 'refresh_xcvr_api') == True:
            try:
                sfp.refresh_xcvr_api()
            except:
                pass

def _wrapper_refresh_driver(physical_port):
    if platform_chassis:
        try:
            sfp = platform_chassis.get_sfp(physical_port)
            if hasattr(sfp, 'refresh_optoe_dev_class') == True:
                sfp.refresh_optoe_dev_class()
        except:
            pass

def _wrapper_get_presence(physical_port):
    if platform_chassis is not None:
        try:
            return platform_chassis.get_sfp(physical_port).get_presence()
        except NotImplementedError:
            pass
    return platform_sfputil.get_presence(physical_port)

def _wrapper_get_transceiver_info(physical_port):
    if platform_chassis is not None:
        try:
            return platform_chassis.get_sfp(physical_port).get_transceiver_info()
        except NotImplementedError:
            pass
    return platform_sfputil.get_transceiver_info_dict(physical_port)

def _wrapper_is_replaceable(physical_port):
    if platform_chassis is not None:
        try:
            return platform_chassis.get_sfp(physical_port).is_replaceable()
        except NotImplementedError:
            pass
    return False

def _wrapper_get_sfp_type(physical_port):
    if platform_chassis:
        try:
            sfp = platform_chassis.get_sfp(physical_port)
        except (NotImplementedError, AttributeError):
            return None
        try:
            return sfp.sfp_type
        except (NotImplementedError, AttributeError):
            pass
    return None

# Update port sfp info in db

def post_port_sfp_info_to_db(logical_port_name, port_mapping, table, transceiver_dict,
                             stop_event=threading.Event(), sfp_insert_events=None):
    ganged_port = False
    ganged_member_num = 1

    physical_port_list = port_mapping.logical_port_name_to_physical_port_list(logical_port_name)
    if physical_port_list is None:
        helper_logger.log_error("No physical ports found for logical port '{}'".format(logical_port_name))
        return PHYSICAL_PORT_NOT_EXIST

    if len(physical_port_list) > 1:
        ganged_port = True

    for physical_port in physical_port_list:
        if stop_event.is_set():
            break

        if not _wrapper_get_presence(physical_port):
            continue

        port_name = get_physical_port_name(logical_port_name, ganged_member_num, ganged_port)
        ganged_member_num += 1
        try:
            port_info_dict = _wrapper_get_transceiver_info(physical_port)
            if port_info_dict is not None:
                is_replaceable = _wrapper_is_replaceable(physical_port)
                transceiver_dict[physical_port] = port_info_dict
                # When 'sfp_insert_events' is specified, it is in one of the transition states.
                # The eeprom data is read on these transition states
                if sfp_insert_events:
                    # Calculate the checksum after reading eeprom data
                    m = hashlib.md5()
                    m.update(str(port_info_dict).encode('utf-8'))
                    current_sfp_info_checksum = m.hexdigest()
                    last_read_eeprom_checksum = sfp_insert_events[physical_port][insert_event_field.CHECKSUM.value]
                    # Update the checksum and set the flag to indicate that the data need to be written to db
                    if current_sfp_info_checksum != last_read_eeprom_checksum:
                        if last_read_eeprom_checksum != 0:
                            helper_logger.log_notice("Port {} sfp info is updated due to the different checksum of sfp info has been detected".format(physical_port))
                        sfp_insert_events[physical_port][insert_event_field.CHECKSUM.value] = current_sfp_info_checksum

                if 'cmis_rev' in port_info_dict:
                    fvs = swsscommon.FieldValuePairs(
                        [('type', '*'+port_info_dict['type']
                            if port_info_dict['vendor_oui'] in CERTIFICATED_VENDOR and port_info_dict['serial'][:2] in CERTIFICATED_SERIAL else port_info_dict['type']),
                        ('vendor_rev', port_info_dict['vendor_rev']),
                        ('serial', port_info_dict['serial']),
                        ('manufacturer', port_info_dict['manufacturer']),
                        ('model', port_info_dict['model']),
                        ('vendor_oui', port_info_dict['vendor_oui']),
                        ('vendor_date', port_info_dict['vendor_date']),
                        ('connector', port_info_dict['connector']),
                        ('encoding', port_info_dict['encoding']),
                        ('ext_identifier', port_info_dict['ext_identifier']),
                        ('ext_rateselect_compliance', port_info_dict['ext_rateselect_compliance']),
                        ('cable_type', port_info_dict['cable_type']),
                        ('cable_length', str(port_info_dict['cable_length'])),
                        ('specification_compliance', port_info_dict['specification_compliance']),
                        ('nominal_bit_rate', str(port_info_dict['nominal_bit_rate'])),
                        ('application_advertisement', port_info_dict['application_advertisement']
                        if 'application_advertisement' in port_info_dict else 'N/A'),
                        ('is_replaceable', str(is_replaceable)),
                        ('dom_capability', port_info_dict['dom_capability']
                        if 'dom_capability' in port_info_dict else 'N/A'),
                        ('cmis_rev', port_info_dict['cmis_rev'] if 'cmis_rev' in port_info_dict else 'N/A'),
                        ('active_firmware', port_info_dict['active_firmware']
                        if 'active_firmware' in port_info_dict else 'N/A'),
                        ('inactive_firmware', port_info_dict['inactive_firmware']
                        if 'inactive_firmware' in port_info_dict else 'N/A'),
                        ('hardware_rev', port_info_dict['hardware_rev']
                        if 'hardware_rev' in port_info_dict else 'N/A'),
                        ('media_interface_code', port_info_dict['media_interface_code']
                        if 'media_interface_code' in port_info_dict else 'N/A'),
                        ('host_electrical_interface', port_info_dict['host_electrical_interface']
                        if 'host_electrical_interface' in port_info_dict else 'N/A'),
                        ('host_lane_count', str(port_info_dict['host_lane_count'])
                        if 'host_lane_count' in port_info_dict else 'N/A'),
                        ('media_lane_count', str(port_info_dict['media_lane_count'])
                        if 'media_lane_count' in port_info_dict else 'N/A'),
                        ('host_lane_assignment_option', str(port_info_dict['host_lane_assignment_option'])
                        if 'host_lane_assignment_option' in port_info_dict else 'N/A'),
                        ('media_lane_assignment_option', str(port_info_dict['media_lane_assignment_option'])
                        if 'media_lane_assignment_option' in port_info_dict else 'N/A'),
                        ('active_apsel_hostlane1', str(port_info_dict['active_apsel_hostlane1'])
                        if 'active_apsel_hostlane1' in port_info_dict else 'N/A'),
                        ('active_apsel_hostlane2', str(port_info_dict['active_apsel_hostlane2'])
                        if 'active_apsel_hostlane2' in port_info_dict else 'N/A'),
                        ('active_apsel_hostlane3', str(port_info_dict['active_apsel_hostlane3'])
                        if 'active_apsel_hostlane3' in port_info_dict else 'N/A'),
                        ('active_apsel_hostlane4', str(port_info_dict['active_apsel_hostlane4'])
                        if 'active_apsel_hostlane4' in port_info_dict else 'N/A'),
                        ('active_apsel_hostlane5', str(port_info_dict['active_apsel_hostlane5'])
                        if 'active_apsel_hostlane5' in port_info_dict else 'N/A'),
                        ('active_apsel_hostlane6', str(port_info_dict['active_apsel_hostlane6'])
                        if 'active_apsel_hostlane6' in port_info_dict else 'N/A'),
                        ('active_apsel_hostlane7', str(port_info_dict['active_apsel_hostlane7'])
                        if 'active_apsel_hostlane7' in port_info_dict else 'N/A'),
                        ('active_apsel_hostlane8', str(port_info_dict['active_apsel_hostlane8'])
                        if 'active_apsel_hostlane8' in port_info_dict else 'N/A'),
                        ('media_interface_technology', port_info_dict['media_interface_technology']
                        if 'media_interface_technology' in port_info_dict else 'N/A'),
                        ('supported_max_tx_power', str(port_info_dict['supported_max_tx_power'])
                        if 'supported_max_tx_power' in port_info_dict else 'N/A'),
                        ('supported_min_tx_power', str(port_info_dict['supported_min_tx_power'])
                        if 'supported_min_tx_power' in port_info_dict else 'N/A'),
                        ('supported_max_laser_freq', str(port_info_dict['supported_max_laser_freq'])
                        if 'supported_max_laser_freq' in port_info_dict else 'N/A'),
                        ('supported_min_laser_freq', str(port_info_dict['supported_min_laser_freq'])
                        if 'supported_min_laser_freq' in port_info_dict else 'N/A')
                    ])
                # else cmis is not supported by the module
                else:
                    fvs = swsscommon.FieldValuePairs([
                        ('type', '*'+port_info_dict['type']
                            if port_info_dict['vendor_oui'] in CERTIFICATED_VENDOR and port_info_dict['serial'][:2] in CERTIFICATED_SERIAL else port_info_dict['type']),
                        ('vendor_rev', port_info_dict['vendor_rev']),
                        ('serial', port_info_dict['serial']),
                        ('manufacturer', port_info_dict['manufacturer']),
                        ('model', port_info_dict['model']),
                        ('vendor_oui', port_info_dict['vendor_oui']),
                        ('vendor_date', port_info_dict['vendor_date']),
                        ('connector', port_info_dict['connector']),
                        ('encoding', port_info_dict['encoding']),
                        ('ext_identifier', port_info_dict['ext_identifier']),
                        ('ext_rateselect_compliance', port_info_dict['ext_rateselect_compliance']),
                        ('cable_type', port_info_dict['cable_type']),
                        ('cable_length', str(port_info_dict['cable_length'])),
                        ('specification_compliance', port_info_dict['specification_compliance']),
                        ('nominal_bit_rate', str(port_info_dict['nominal_bit_rate'])),
                        ('application_advertisement', port_info_dict['application_advertisement']
                        if 'application_advertisement' in port_info_dict else 'N/A'),
                        ('is_replaceable', str(is_replaceable)),
                        ('dom_capability', port_info_dict['dom_capability']
                        if 'dom_capability' in port_info_dict else 'N/A')
                    ])
                table.set(port_name, fvs)
            else:
                _wrapper_refresh_driver(physical_port)
                helper_logger.log_notice("{}: eeprom not ready".format(port_name))
                return SFP_EEPROM_NOT_READY

        except NotImplementedError:
            helper_logger.log_error("This functionality is currently not implemented for this platform")
            sys.exit(NOT_IMPLEMENTED_ERROR)


class CmisManagerTask:

    CMIS_MAX_RETRIES     = 5
    CMIS_DEF_EXPIRED     = 150 # seconds, default expiration time
    CMIS_MODULE_TYPES    = ['QSFP-DD', 'QSFP_DD', 'OSFP']
    CMIS_NUM_CHANNELS    = 8

    CMIS_STATE_UNKNOWN   = 'UNKNOWN'
    CMIS_STATE_INSERTED  = 'INSERTED'
    CMIS_STATE_DP_DEINIT = 'DP_DEINIT'
    CMIS_STATE_AP_CONF   = 'AP_CONFIGURED'
    CMIS_STATE_DP_ACTIVATE = 'DP_ACTIVATION'
    CMIS_STATE_DP_INIT   = 'DP_INIT'
    CMIS_STATE_DP_TXON   = 'DP_TXON'
    CMIS_STATE_READY     = 'READY'
    CMIS_STATE_REMOVED   = 'REMOVED'
    CMIS_STATE_FAILED    = 'FAILED'

    def __init__(self, port_mapping, skip_cmis_mgr=False):
        self.task_stopping_event = multiprocessing.Event()
        self.task_process = None
        self.port_dict = {}
        self.cmis_port = []
        self.appl_code = 4
        self.port_mapping = copy.deepcopy(port_mapping)
        self.isPortInitDone = False
        self.isPortConfigDone = False
        self.skip_cmis_mgr = skip_cmis_mgr
        self.default_port_dict = self.get_default_port_dict()
        self.xcvr_table_helper = XcvrTableHelper()

    def get_default_port_dict(self):
        default_port_dict = {}
        platform_json_file = device_info.get_path_to_port_config_file()
        if not os.path.isfile(platform_json_file) or not platform_json_file.endswith('.json'):
            return None
        else:
            platform_dict = readJson(platform_json_file)
            for intf in platform_dict['interfaces']:
                default_port_dict[intf] = {}
                default_port_dict[intf]['lanes'] = platform_dict['interfaces'][intf]['lanes']
        return default_port_dict

    def log_notice(self, message):
        helper_logger.log_notice("CMIS: {}".format(message))

    def log_error(self, message):
        helper_logger.log_error("CMIS: {}".format(message))

    def log_warning(self, message):
        helper_logger.log_warning("CMIS: {}".format(message))

    def on_port_update_event(self, port_change_event):
        if port_change_event.event_type not in [port_change_event.PORT_SET, port_change_event.PORT_DEL, port_change_event.PORT_INSERT]:
            return

        lport = port_change_event.port_name
        pport = port_change_event.port_index

        if lport in ['PortInitDone']:
            self.isPortInitDone = True
            return

        if lport in ['PortConfigDone']:
            self.isPortConfigDone = True
            return

        # Skip if it's not a physical port
        if not lport.startswith('Ethernet'):
            return

        # reinit sfp when transceiver insert
        if port_change_event.event_type == port_change_event.PORT_INSERT:
            if lport in self.port_dict:
                pport = self.port_dict[lport]['index']
                _wrapper_update_sfp_type(pport)
                _wrapper_refresh_xcvr_api(pport)
                state = self.port_dict[lport]['cmis_state']
                # Don't do reset when it is initializing
                if state in [self.CMIS_STATE_UNKNOWN,
                             self.CMIS_STATE_FAILED,
                             self.CMIS_STATE_READY,
                             self.CMIS_STATE_REMOVED]:
                    self.reset_cmis_init(lport, 0)

        # Skip if the physical index is not available
        if pport == None:
            return

        # When pport is larger than 0, it means the content of the port change event is related to
        # port attribute changes.
        # When pport is negative, it means the port change event could be PORT_DEL or PORT_INSERT.
        if pport >=0:
            # Skip if the port/cage type is not a CMIS
            ptype = _wrapper_get_sfp_type(pport)
            if ptype in self.CMIS_MODULE_TYPES:
                if lport not in self.cmis_port:
                    self.cmis_port.append(lport)
            else:
                if lport in self.cmis_port:
                    self.cmis_port.remove(lport)

        if lport not in self.port_dict:
            self.port_dict[lport] = {}

        if port_change_event.event_type == port_change_event.PORT_SET:
            need_reset_cmis = False
            if pport >= 0:
                self.port_dict[lport]['index'] = pport
            if port_change_event.port_dict is not None and 'speed' in port_change_event.port_dict:
                if 'speed' not in self.port_dict[lport] or self.port_dict[lport]['speed'] != port_change_event.port_dict['speed']:
                    self.port_dict[lport]['speed'] = port_change_event.port_dict['speed']
                    need_reset_cmis = True
            if port_change_event.port_dict is not None and 'lanes' in port_change_event.port_dict:
                if 'lanes' not in self.port_dict[lport] or self.port_dict[lport]['lanes'] != port_change_event.port_dict['lanes']:
                    self.port_dict[lport]['lanes'] = port_change_event.port_dict['lanes']
                    need_reset_cmis = True
            if port_change_event.port_dict is not None and 'laser_freq' in port_change_event.port_dict:
                if 'laser_freq' not in self.port_dict[lport] or self.port_dict[lport]['laser_freq'] != int(port_change_event.port_dict['laser_freq']):
                    self.port_dict[lport]['laser_freq'] = int(port_change_event.port_dict['laser_freq'])
                    need_reset_cmis = True
            if port_change_event.port_dict is not None and 'grid_space' in port_change_event.port_dict:
                if 'grid_space' not in self.port_dict[lport] or self.port_dict[lport]['grid_space'] != int(port_change_event.port_dict['grid_space']):
                    self.port_dict[lport]['grid_space'] = int(port_change_event.port_dict['grid_space'])
                    need_reset_cmis = True
            if port_change_event.port_dict is not None and 'tx_power' in port_change_event.port_dict:
                if 'tx_power' not in self.port_dict[lport] or self.port_dict[lport]['tx_power'] != float(port_change_event.port_dict['tx_power']):
                    self.port_dict[lport]['tx_power'] = float(port_change_event.port_dict['tx_power'])
                    need_reset_cmis = True
            if port_change_event.port_dict is not None and 'parent_port' in port_change_event.port_dict:
                self.port_dict[lport]['parent_port'] = port_change_event.port_dict['parent_port']
            if need_reset_cmis == True:
                self.reset_cmis_init(lport, 0)
            if not self.port_mapping.is_logical_port(lport):
                port_change_event.event_type = port_change_event.PORT_ADD
                self.port_mapping.handle_port_change_event(port_change_event)
        elif port_change_event.event_type == port_change_event.PORT_DEL:
            self.port_dict[lport] = {}
            self.port_mapping.handle_port_change_event(port_change_event)

    def get_interface_speed(self, ifname):
        """
        Get the port speed from the host interface name

        Args:
            ifname: String, interface name

        Returns:
            Integer, the port speed if success otherwise 0
        """
        # see HOST_ELECTRICAL_INTERFACE of sff8024.py
        speed = 0
        if '400G' in ifname:
            speed = 400000
        elif '200G' in ifname:
            speed = 200000
        elif '100G' in ifname or 'CAUI-4' in ifname:
            speed = 100000
        elif '50G' in ifname or 'LAUI-2' in ifname:
            speed = 50000
        elif '40G' in ifname or 'XLAUI' in ifname or 'XLPPI' in ifname:
            speed = 40000
        elif '25G' in ifname:
            speed = 25000
        elif '10G' in ifname or 'SFI' in ifname or 'XFI' in ifname:
            speed = 10000
        elif '1000BASE' in ifname:
            speed = 1000
        return speed

    def get_cmis_application_desired(self, api, channel, speed):
        """
        Get the CMIS application code that matches the specified host side configurations

        Args:
            api:
                XcvrApi object
            channel:
                Integer, a bitmask of the lanes on the host side
                e.g. 0x5 for lane 0 and lane 2.
            speed:
                Integer, the port speed of the host interface

        Returns:
            Integer, the transceiver-specific application code
        """
        if speed == 0 or channel == 0:
            return 0

        host_lane_count = 0
        for lane in range(self.CMIS_NUM_CHANNELS):
            if ((1 << lane) & channel) == 0:
                continue
            host_lane_count += 1

        appl_code = 0
        appl_dict = api.get_application_advertisement()
        for c in appl_dict.keys():
            d = appl_dict[c]
            if d.get('host_lane_count') != host_lane_count:
                continue
            if self.get_interface_speed(d.get('host_electrical_interface_id')) != speed:
                continue
            appl_code = c
            break
        #fix temp
        #mishaal---------------------
        appl_code = 3

        return (appl_code & 0xf)

    def is_cmis_application_update_required(self, api, channel, speed):
        """
        Check if the CMIS application update is required

        Args:
            api:
                XcvrApi object
            channel:
                Integer, a bitmask of the lanes on the host side
                e.g. 0x5 for lane 0 and lane 2.
            speed:
                Integer, the port speed of the host interface

        Returns:
            Boolean, true if application update is required otherwise false
        """
        if speed == 0 or channel == 0 or api.is_flat_memory():
            return False

        app_new = self.get_cmis_application_desired(api, channel, speed)
        #if app_new != 1:
        #    self.log_notice("Non-default application is not supported")
        #    return False

        app_old = 0
        for lane in range(self.CMIS_NUM_CHANNELS):
            if ((1 << lane) & channel) == 0:
                continue
            app = api.get_application(lane)
            self.log_notice("{}: lane {} has application code {}".format("Ethernet16", lane, app))
            if app_old == 0:
                app_old = app
            elif app_old != app:
                self.log_notice("Not all the lanes are in the same application mode")
                self.log_notice("Forcing application update...")
                return True

        if app_old == app_new:
            skip = True
            dp_state = api.get_datapath_state()
            conf_state = api.get_config_datapath_hostlane_status()
            for lane in range(self.CMIS_NUM_CHANNELS):
                if ((1 << lane) & channel) == 0:
                    continue
                name = "DP{}State".format(lane + 1)
                if dp_state[name] != 'DataPathActivated':
                    skip = False
                    break
                name = "ConfigStatusLane{}".format(lane + 1)
                # After HW init done, the conf state would be ConfigUndefined
                if conf_state[name] != 'ConfigSuccess' and conf_state[name] != 'ConfigUndefined':
                    skip = False
                    break
            return (not skip)

        return True

    def is_cmis_hw_initializing(self, api, channel):
        control_mode = api.get_lpmode_control()
        if control_mode != 'LowPwrAllowRequestHW':
            return False
        mdstate = api.get_module_state()
        if mdstate == 'ModulePwrUp':
            return True
        dp_state = api.get_datapath_state()
        for lane in range(self.CMIS_NUM_CHANNELS):
            if ((1 << lane) & channel) == 0:
                continue
            name = "DP{}State".format(lane + 1)
            if dp_state[name] != 'DataPathDeactivated' and dp_state[name] != 'DataPathActivated':
                return True

        return False

    def reset_cmis_init(self, lport, retries=0):
        self.port_dict[lport]['cmis_state'] = self.CMIS_STATE_INSERTED
        self.port_dict[lport]['cmis_retries'] = retries
        self.port_dict[lport]['cmis_expired'] = None # No expiration

    def test_module_state(self, api, states):
        """
        Check if the CMIS module is in the specified state

        Args:
            api:
                XcvrApi object
            states:
                List, a string list of states

        Returns:
            Boolean, true if it's in the specified state, otherwise false
        """
        return api.get_module_state() in states

    def test_config_active(self, api, channel, appl):
        apsel_dict = api.get_active_apsel_hostlane()
        for lane in range(self.CMIS_NUM_CHANNELS):
            if ((1 << lane) & channel) != 0:
                if appl != apsel_dict["%s%d" % (consts.ACTIVE_APSEL_HOSTLANE, lane+1)]:
                    return False
        return True


    def test_config_error(self, api, channel, states):
        """
        Check if the CMIS configuration states are in the specified state

        Args:
            api:
                XcvrApi object
            channel:
                Integer, a bitmask of the lanes on the host side
                e.g. 0x5 for lane 0 and lane 2.
            states:
                List, a string list of states

        Returns:
            Boolean, true if all lanes are in the specified state, otherwise false
        """
        done = True
        cerr = api.get_config_datapath_hostlane_status()
        for lane in range(self.CMIS_NUM_CHANNELS):
            if ((1 << lane) & channel) == 0:
                continue
            key = "ConfigStatusLane{}".format(lane + 1)
            if cerr[key] not in states:
                done = False
                break

        return done

    def test_datapath_state(self, api, channel, states):
        """
        Check if the CMIS datapath states are in the specified state

        Args:
            api:
                XcvrApi object
            channel:
                Integer, a bitmask of the lanes on the host side
                e.g. 0x5 for lane 0 and lane 2.
            states:
                List, a string list of states

        Returns:
            Boolean, true if all lanes are in the specified state, otherwise false
        """
        done = True
        dpstate = api.get_datapath_state()
        for lane in range(self.CMIS_NUM_CHANNELS):
            if ((1 << lane) & channel) == 0:
                continue
            key = "DP{}State".format(lane + 1)
            if dpstate[key] not in states:
                done = False
                break

        return done

    def get_configured_laser_freq_from_db(self, lport):
        """
           Return the laser frequency configured by user in CONFIG_DB's PORT table
        """
        freq = 0
        grid_space = 0
        asic_index = self.port_mapping.get_asic_id_for_logical_port(lport)
        port_tbl = self.xcvr_table_helper.get_cfg_port_tbl(asic_index)

        found, port_info = port_tbl.get(lport)
        self.log_notice("AAAAAA port dict: {}".format(dict(port_info)))
        if found and 'laser_freq' in dict(port_info):
            freq = dict(port_info)['laser_freq']
        if found and 'grid_space' in dict(port_info):
            grid_space = dict(port_info)['grid_space']
        return int(freq), int(grid_space)

    def get_configured_tx_power_from_db(self, lport):
        """
           Return the Tx power configured by user in CONFIG_DB's PORT table
        """
        power = 0
        asic_index = self.port_mapping.get_asic_id_for_logical_port(lport)
        port_tbl = self.xcvr_table_helper.get_cfg_port_tbl(asic_index)

        found, port_info = port_tbl.get(lport)
        if found and 'tx_power' in dict(port_info):
            power = dict(port_info)['tx_power']
        return float(power)

    def configure_tx_output_power(self, api, lport, tx_power):
        min_p, max_p = api.get_supported_power_config()
        if tx_power < min_p:
           self.log_error("{} configured tx power {} < minimum power {} supported".format(lport, tx_power, min_p))
           return False
        if tx_power > max_p:
           self.log_error("{} configured tx power {} > maximum power {} supported".format(lport, tx_power, max_p))
           return False
        return api.set_tx_power(tx_power)

    def verify_config_laser_frequency(self, api, lport, freq, grid):
        _, _,  _, lowf, highf = api.get_supported_freq_config()
        if freq < lowf:
            self.log_error("{} configured freq:{} GHz is lower than the supported freq:{} GHz".format(lport, freq, lowf))
            return False
        if freq > highf:
            self.log_error("{} configured freq:{} GHz is higher than the supported freq:{} GHz".format(lport, freq, highf))
            return False

        if grid == 75:
            channel_number = int(round((freq - 193100)/25))
            if channel_number % 3 != 0:
                self.log_error("{} configured freq:{} GHz is not supported in grid {} GHz".format(lport, freq, grid))
                return False
        return True

    def configure_laser_frequency(self, api, lport, freq, grid):
        if api.get_tuning_in_progress():
            self.log_warning("{} Tuning in progress, channel selection may fail!".format(lport))
        try:
            return api.set_laser_freq(freq, grid)
        except:
            return False


    #cialone
    def task_worker(self):
        self.log_notice("Starting XXXX.......")
        print("task worker")
        # APPL_DB for CONFIG updates, and STATE_DB for insertion/removal
        #sel, asic_context = port_mapping.subscribe_port_update_event(['APPL_DB', 'STATE_DB'])
        import sonic_platform.platform
        import sonic_platform_base.sonic_sfp.sfputilhelper
        platform_chassis = sonic_platform.platform.Platform().get_chassis()
        #mishaal--------------------
        self.port_dict = {'Ethernet16' :
                              {'index': 3, 'speed': '400000', 'lanes': '81,82,83,84,85,86,87,88', 
                               'laser_freq': 195500, 'grid_space': 100, 'parent_port': 'Ethernet16', 
                               'cmis_state': 'INSERTED', 'cmis_retries': 0, 'cmis_expired': None, 
                               'tx_power': 0.0}}
        state = self.port_dict['Ethernet16'].get('cmis_state', self.CMIS_STATE_UNKNOWN)

        pport = int(self.port_dict['Ethernet16'].get('index', "-1"))
        speed = int(self.port_dict['Ethernet16'].get('speed', "0"))
        lanes = self.port_dict['Ethernet16'].get('lanes', "").strip()

        # Desired port speed on the host side
        host_speed = speed

        parent_port = self.port_dict["Ethernet16"]['parent_port']
        # Convert the physical lane list into a logical lanemask
        host_lanes = 0
        phys_lanes = lanes.split(',')
        parent_port_phys_lanes = self.default_port_dict[parent_port]['lanes'].split(',')
        for i in range(len(parent_port_phys_lanes)):
                    if parent_port_phys_lanes[i] in phys_lanes:
                        host_lanes |= (1 << i)
                    else:
                        host_lanes << 1
        all_host_lanes = 0
        for i in range(self.CMIS_NUM_CHANNELS):
                all_host_lanes |= (1 << i)

        # double-check the HW presence before moving forward
        sfp = platform_chassis.get_sfp(pport)
        api = sfp.get_xcvr_api()

        stay_in = 1
        #while not self.task_stopping_event.is_set():
        while stay_in:
            now = datetime.datetime.now()
            expired = self.port_dict['Ethernet16'].get('cmis_expired')
            retries = self.port_dict['Ethernet16'].get('cmis_retries', 0)
            self.log_notice("{}: {}G, lanemask=0x{:x}, state={}, retries={}".format(
                            'Ethernet16', int(speed/1000), host_lanes, state, retries))
            #self.log_notice("{}: FSM new cycle")
            time.sleep(1)
            print(retries)
            print(self.CMIS_MAX_RETRIES)
            if retries > self.CMIS_MAX_RETRIES:
                self.log_error("{}: FAILED".format('Ethernet16'))
                self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_FAILED
                continue            
            try:
                # CMIS state transitions
                if state == self.CMIS_STATE_INSERTED:
                    # Configure the target output power if ZR module
                    if 'Ethernet16' == parent_port and api.is_coherent_module():
                        tx_power = self.port_dict['Ethernet16']['tx_power']
                        # Prevent configuring same tx power multiple times
                        if 0 != tx_power and tx_power != api.get_tx_config_power():
                            if True != self.configure_tx_output_power(api, 'Ethernet16', tx_power):
                                self.log_error("{} failed to configure Tx power = {}".format('Ethernet16', tx_power))
                            else:
                                self.log_notice("{} Successfully configured Tx power = {}".format('Ethernet16', tx_power))

                    appl = self.get_cmis_application_desired(api, host_lanes, host_speed)
                    self.log_notice("{}: application code {}".format('Ethernet16', appl))
                    if appl < 1:
                        self.log_error("{}: no suitable app for the port".format('Ethernet16'))
                        self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_FAILED
                        continue

                    need_update = self.is_cmis_application_update_required(api, host_lanes, host_speed)
                    self.log_notice("{}: need update {}".format('Ethernet16', need_update))
                    # For ZR module, Datapath needes to be re-initlialized on new channel selection
                    if 'Ethernet16' == parent_port and api.is_coherent_module():
                        # If the freq is changed, it needs to reinit datapase include cmis application.
                        # Breakout port needs to update if freq of parent port is changed.
                        freq = self.port_dict['Ethernet16']['laser_freq']
                        grid = self.port_dict['Ethernet16']['grid_space']
                        # If user requested frequency is NOT the same as configured on the module
                        # force datapath re-initialization
                        if (0 != freq and freq != api.get_laser_config_freq()) or \
                            (0 != grid and grid != api.get_freq_grid()):
                            if self.verify_config_laser_frequency(api, 'Ethernet16', freq, grid) == True:
                                need_update = True
                    if not need_update:
                        # No application updates
                        self.log_notice("{}: READY".format('Ethernet16'))
                        self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_READY
                        stay_in = 0

                    # Save the current active apsel in the xcvr into self.port_dict at state
                    # 'CMIS_STATE_INSERTED'. It is required to update sfp info in redisDB if
                    # the apsel is changed during the cmis init procedure. The apsel saved in
                    # port_dict will be used to check whether the sfp info needs to be updated
                    # at state 'CMIS_STATE_DP_TXON'.
                    self.port_dict['Ethernet16']['apsel'] = api.get_active_apsel_hostlane()
                    self.log_notice("{}: apsel={}".format('Ethernet16', self.port_dict['Ethernet16']['apsel']))
                    # D.2.2 Software Deinitialization
                    # set low power mode would be delayed when cmis hw initializing is in progress
                    if 'Ethernet16' == parent_port and self.is_cmis_hw_initializing(api, all_host_lanes):
                        self.log_notice("{}: delay SW init due to HW init is executing".format('Ethernet16'))
                        continue
                    self.log_notice("{}: force Datapath reinit".format('Ethernet16'))
                    self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_DP_DEINIT
                    state = self.CMIS_STATE_DP_DEINIT
                    self.log_notice(self.port_dict['Ethernet16'])
                elif state == self.CMIS_STATE_DP_DEINIT:
                    if 'Ethernet16' != parent_port:
                        # For breakout ports, it should only set low power mode for once.
                        # Bypass set low power for non-parent ports.
                        if self.port_dict[parent_port]['cmis_state'] in [self.CMIS_STATE_AP_CONF,
                                                                            self.CMIS_STATE_DP_INIT,
                                                                            self.CMIS_STATE_DP_TXON,
                                                                            self.CMIS_STATE_DP_ACTIVATE,
                                                                            self.CMIS_STATE_READY]:
                            self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_AP_CONF
                        continue
                    # If datapath state is transient state, set low power mode would be delay.
                    if self.test_datapath_state(api, host_lanes, ['DataPathInit', 'DataPathDeInit', 'DataPathTxTurnOn', 'DataPathTxTurnOff']):
                        self.log_notice("{}: wait datapath finish transient state.".format('Ethernet16'))
                        continue
                    #to be deactivated
                    #cialone
                    api.set_lpmode(True)
                    if not self.test_module_state(api, ['ModuleLowPwr']):
                        self.log_notice("{}: unable to enter low-power mode".format('Ethernet16'))
                        self.port_dict['Ethernet16']['cmis_retries'] = retries + 1
                        continue
                    #
                    api.set_datapath_deinit(all_host_lanes)

                    # D.1.3 Software Configuration and Initialization
                    if api.get_tx_disable_support() == True:
                        if not api.tx_disable_channel(all_host_lanes, True):
                            self.log_notice("{}: unable to turn off tx power".format('Ethernet16'))
                            self.port_dict['Ethernet16']['cmis_retries'] = retries + 1
                            continue
                    api.set_lpmode(False)

                    # TODO: Use fine grained time when the CMIS memory map is available
                    self.port_dict['Ethernet16']['cmis_expired'] = now + datetime.timedelta(seconds=self.CMIS_DEF_EXPIRED)
                    self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_AP_CONF
                    state = self.CMIS_STATE_AP_CONF
                elif state == self.CMIS_STATE_AP_CONF:
                    if not self.test_module_state(api, ['ModuleReady']):
                        if (expired is not None) and (expired <= now):
                            self.log_notice("{}: timeout for 'ModuleReady'".format('Ethernet16'))
                            self.reset_cmis_init('Ethernet16', retries + 1)
                        continue
                    if not self.test_datapath_state(api, host_lanes, ['DataPathDeinit', 'DataPathDeactivated']):
                        if (expired is not None) and (expired <= now):
                            self.log_notice("{}: timeout for 'DataPathDeinit'".format('Ethernet16'))
                            self.reset_cmis_init('Ethernet16', retries + 1)
                        continue

                    # For ZR module, configure the laser frequency when Datapath is in Deactivated state
                    if 'Ethernet16' == parent_port and api.is_coherent_module():
                        freq = self.port_dict['Ethernet16']['laser_freq']
                        grid = self.port_dict['Ethernet16']['grid_space']
                        if 0 != freq and 0 != grid:
                            if True != self.configure_laser_frequency(api, 'Ethernet16', freq, grid):
                                self.log_error("{} failed to configure laser frequency {} GHz grid space {} GHz".format('Ethernet16', freq, grid))
                            else:
                                self.log_notice("{} configured laser frequency {} GHz grid space {} GHz".format('Ethernet16', freq, grid))

                    # D.1.3 Software Configuration and Initialization
                    appl = self.get_cmis_application_desired(api, host_lanes, host_speed)
                    self.log_notice("{}: application code {}".format('Ethernet16', appl))
                    if appl < 1:
                        self.log_error("{}: no suitable app for the port".format('Ethernet16'))
                        self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_FAILED
                        continue

                    #cialone
                    api.set_application(host_lanes, appl)
                    api.apply_application(host_lanes)
                    # TODO: Use fine grained time when the CMIS memory map is available
                    self.port_dict['Ethernet16']['cmis_expired'] = now + datetime.timedelta(seconds=self.CMIS_DEF_EXPIRED)
                    self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_DP_INIT
                    state = self.CMIS_STATE_DP_INIT
                elif state == self.CMIS_STATE_DP_INIT:
                    if not self.test_config_error(api, host_lanes, ['ConfigSuccess']):
                        if (expired is not None) and (expired <= now):
                            self.log_notice("{}: timeout for 'ConfigSuccess'".format('Ethernet16'))
                            self.reset_cmis_init('Ethernet16', retries + 1)
                        continue

                    # D.1.3 Software Configuration and Initialization
                    # Sometimes even the dataplane is ConfigSuccess, but the Active set are not the same as Staged set
                    # Workaround: apply Staged set to Active set again
                    appl = self.get_cmis_application_desired(api, host_lanes, host_speed)
                    if not self.test_config_active(api, host_lanes, appl):
                        if (expired is not None) and (expired <= now):
                            self.log_notice("{}: timeout for 'Config Active'".format('Ethernet16'))
                            self.reset_cmis_init('Ethernet16', retries + 1)
                        else:
                            api.apply_application(host_lanes)
                            self.log_notice("{}: re-apply application".format('Ethernet16'))
                        continue
                    api.set_datapath_init(host_lanes)

                    # TODO: Use fine grained time when the CMIS memory map is available
                    self.port_dict['Ethernet16']['cmis_expired'] = now + datetime.timedelta(seconds=self.CMIS_DEF_EXPIRED)
                    self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_DP_TXON
                    state = self.CMIS_STATE_DP_TXON
                elif state == self.CMIS_STATE_DP_TXON:
                    # For CMIS4.0, DataPathInitialized is transient state. It would enter DataPathActivated automatically.
                    # For CMIS5.0, the module would stay in DataPathInitialized mode. After host enable TX, it would enter DataPathActivated.
                    if not self.test_datapath_state(api, host_lanes, ['DataPathInitialized', 'DataPathActivated']):
                        if (expired is not None) and (expired <= now):
                            self.log_notice("{}: timeout for 'DataPathInitialized'".format('Ethernet16'))
                            self.reset_cmis_init('Ethernet16', retries + 1)
                        continue

                    # D.1.3 Software Configuration and Initialization
                    if api.get_tx_disable_support() == True:
                        if not api.tx_disable_channel(host_lanes, False):
                            self.log_notice("{}: unable to turn on tx power".format('Ethernet16'))
                            self.reset_cmis_init('Ethernet16', retries + 1)
                            continue

                    # TODO: Use fine grained timeout when the CMIS memory map is available
                    self.port_dict['Ethernet16']['cmis_expired'] = now + datetime.timedelta(seconds=self.CMIS_DEF_EXPIRED)
                    self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_DP_ACTIVATE
                    state = self.CMIS_STATE_DP_ACTIVATE
                elif state == self.CMIS_STATE_DP_ACTIVATE:
                    if not self.test_datapath_state(api, host_lanes, ['DataPathActivated']):
                        if (expired is not None) and (expired <= now):
                            self.log_notice("{}: timeout for 'DataPathActivated'".format('Ethernet16'))
                            self.reset_cmis_init('Ethernet16', retries + 1)
                        continue

                    self.log_notice("{}: READY".format('Ethernet16'))
                    self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_READY

                    if 'Ethernet16' == parent_port:
                        new_apsel_dict = api.get_active_apsel_hostlane()
                        self.log_notice("{}: new apsel={} old apsel={}".format('Ethernet16',new_apsel_dict, self.port_dict['Ethernet16']['apsel']))
  
                        #cialone
                        '''
                        if new_apsel_dict != self.port_dict['Ethernet16']['apsel']:
                            transceiver_dict = {}
                            asic_index = self.port_mapping.get_asic_id_for_logical_port('Ethernet16')
                            # post_port_sfp_info_to_db will update all of the breakout ports
                            # belong to the same parent port
                            post_port_sfp_info_to_db('Ethernet16', self.port_mapping, self.xcvr_table_helper.get_intf_tbl(asic_index),
                                                    transceiver_dict, self.task_stopping_event)
                        '''
                    stay_in = 0        

            except (NotImplementedError, AttributeError):
                self.log_error(traceback.format_exc())
                self.log_error("{}: internal errors".format('Ethernet16'))
                self.port_dict['Ethernet16']['cmis_state'] = self.CMIS_STATE_FAILED

        self.log_notice("Configuration completed")

    def task_run(self):
        print("task run")
        self.task_process = multiprocessing.Process(target=self.task_worker)
        if self.task_process is not None:
            self.task_process.start()

    def task_stop(self):
        self.task_stopping_event.set()
        if self.task_process is not None:
            self.task_process.join()
            self.task_process = None

# Thread wrapper class to update dom info periodically

class XcvrTableHelper:
    def __init__(self):
        self.int_tbl, self.dom_tbl, self.status_tbl, self.app_port_tbl, \
            self.cfg_port_tbl, self.state_port_tbl = {}, {}, {}, {}, {}, {}
        self.state_db = {}
        self.cfg_db = {}
        self.namespaces = multi_asic.get_front_end_namespaces()
        for namespace in self.namespaces:
            asic_id = multi_asic.get_asic_index_from_namespace(namespace)
            self.state_db[asic_id] = daemon_base.db_connect("STATE_DB", namespace)
            self.int_tbl[asic_id] = swsscommon.Table(self.state_db[asic_id], TRANSCEIVER_INFO_TABLE)
            self.dom_tbl[asic_id] = swsscommon.Table(self.state_db[asic_id], TRANSCEIVER_DOM_SENSOR_TABLE)
            self.status_tbl[asic_id] = swsscommon.Table(self.state_db[asic_id], TRANSCEIVER_STATUS_TABLE)
            self.state_port_tbl[asic_id] = swsscommon.Table(self.state_db[asic_id], swsscommon.STATE_PORT_TABLE_NAME)
            appl_db = daemon_base.db_connect("APPL_DB", namespace)
            self.app_port_tbl[asic_id] = swsscommon.ProducerStateTable(appl_db, swsscommon.APP_PORT_TABLE_NAME)
            self.cfg_db[asic_id] = daemon_base.db_connect("CONFIG_DB", namespace)
            self.cfg_port_tbl[asic_id] = swsscommon.Table(self.cfg_db[asic_id], swsscommon.CFG_PORT_TABLE_NAME)

    def get_intf_tbl(self, asic_id):
        return self.int_tbl[asic_id]

    def get_dom_tbl(self, asic_id):
        return self.dom_tbl[asic_id]

    def get_status_tbl(self, asic_id):
        return self.status_tbl[asic_id]

    def get_app_port_tbl(self, asic_id):
        return self.app_port_tbl[asic_id]

    def get_state_db(self, asic_id):
        return self.state_db[asic_id]

    def get_cfg_port_tbl(self, asic_id):
        return self.cfg_port_tbl[asic_id]

    def get_state_port_tbl(self, asic_id):
        return self.state_port_tbl[asic_id]

#def init2(self):
def init2():
    global platform_sfputil
    global platform_chassis
    print("Start daemon init...")

    # Load new platform api class
    try:
        import sonic_platform.platform
        import sonic_platform_base.sonic_sfp.sfputilhelper
        platform_chassis = sonic_platform.platform.Platform().get_chassis()
        print("chassis loaded {}".format(platform_chassis))
        # we have to make use of sfputil for some features
        # even though when new platform api is used for all vendors.
        # in this sense, we treat it as a part of new platform api.
        # we have already moved sfputil to sonic_platform_base
        # which is the root of new platform api.
        platform_sfputil = sonic_platform_base.sonic_sfp.sfputilhelper.SfpUtilHelper()
    except Exception as e:
        print("Failed to load chassis due to {}".format(repr(e)))
    '''
    # Load platform specific sfputil class
    if platform_chassis is None or platform_sfputil is None:
        try:
            platform_sfputil = self.load_platform_util(PLATFORM_SPECIFIC_MODULE_NAME, PLATFORM_SPECIFIC_CLASS_NAME)
        except Exception as e:
            print("Failed to load sfputil: {}".format(str(e)), True)
            sys.exit(SFPUTIL_LOAD_ERROR)
    '''


# Initialize daemon
def init(self):
    global platform_sfputil
    global platform_chassis

    self.log_info("Start daemon init...")

    # Load new platform api class
    try:
        import sonic_platform.platform
        import sonic_platform_base.sonic_sfp.sfputilhelper
        platform_chassis = sonic_platform.platform.Platform().get_chassis()
        self.log_info("chassis loaded {}".format(platform_chassis))
        # we have to make use of sfputil for some features
        # even though when new platform api is used for all vendors.
        # in this sense, we treat it as a part of new platform api.
        # we have already moved sfputil to sonic_platform_base
        # which is the root of new platform api.
        platform_sfputil = sonic_platform_base.sonic_sfp.sfputilhelper.SfpUtilHelper()
    except Exception as e:
        self.log_warning("Failed to load chassis due to {}".format(repr(e)))

    # Load platform specific sfputil class
    if platform_chassis is None or platform_sfputil is None:
        try:
            platform_sfputil = self.load_platform_util(PLATFORM_SPECIFIC_MODULE_NAME, PLATFORM_SPECIFIC_CLASS_NAME)
        except Exception as e:
            self.log_error("Failed to load sfputil: {}".format(str(e)), True)
            sys.exit(SFPUTIL_LOAD_ERROR)

    if multi_asic.is_multi_asic():
        # Load the namespace details first from the database_global.json file.
        swsscommon.SonicDBConfig.initializeGlobalConfig()

    # Initialize xcvr table helper
    self.xcvr_table_helper = XcvrTableHelper()

    self.load_media_settings()

    warmstart = swsscommon.WarmStart()
    warmstart.initialize("xcvrd", "pmon")
    warmstart.checkWarmStart("xcvrd", "pmon", False)
    is_warm_start = warmstart.isWarmStart()

    # Make sure this daemon started after all port configured
    self.log_info("Wait for port config is done")
    for namespace in self.xcvr_table_helper.namespaces:
        self.wait_for_port_config_done(namespace)

    port_mapping_data = port_mapping.get_port_mapping()

    '''# Post all the current interface dom/sfp info to STATE_DB
    self.log_info("Post all port DOM/SFP info to DB")
    retry_eeprom_set = post_port_sfp_dom_info_to_db(is_warm_start, port_mapping_data, self.xcvr_table_helper, self.stop_event)

    # Init port sfp status table
    self.log_info("Init port sfp status table")
    init_port_sfp_status_tbl(port_mapping_data, self.xcvr_table_helper, self.stop_event)

    # Init port y_cable status table
    y_cable_helper.init_ports_status_for_y_cable(
        platform_sfputil, platform_chassis, self.y_cable_presence, port_mapping_data, self.stop_event)'''
    
    return port_mapping_data, retry_eeprom_set
    
if __name__ == '__main__':
    #port_mapping_data, retry_eeprom_set = init()
    init2()
    port_mapping_data = port_mapping.get_port_mapping()
    cmis_manager = CmisManagerTask(port_mapping_data, False)
    cmis_manager.task_run()

import os
import struct
import subprocess
import json
import fcntl
from mmap import *
from sonic_py_common import device_info
from sonic_py_common import logger
from threading import Lock
from typing import cast

HOST_CHK_CMD = "which systemctl > /dev/null 2>&1"
EMPTY_STRING = ""


class APIHelper():

    def __init__(self):
        (self.platform, self.hwsku) = device_info.get_platform_and_hwsku()

    def is_host(self):
        return os.system(HOST_CHK_CMD) == 0

    def pci_get_value(self, resource, offset):
        status = True
        result = ""
        try:
            fd = os.open(resource, os.O_RDWR)
            mm = mmap(fd, 0)
            mm.seek(int(offset))
            read_data_stream = mm.read(4)
            result = struct.unpack('I', read_data_stream)
        except Exception:
            status = False
        return status, result

    def run_command(self, cmd):
        status = True
        result = ""
        try:
            p = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            raw_data, err = p.communicate()
            if err == '':
                result = raw_data.strip()
        except Exception:
            status = False
        return status, result

    def run_interactive_command(self, cmd):
        try:
            os.system(cmd)
        except Exception:
            return False
        return True

    def read_txt_file(self, file_path):
        try:
            with open(file_path, encoding='unicode_escape', errors='replace') as fd:
                data = fd.read()
                ret = data.strip()
                if len(ret) > 0:
                    return ret
        except IOError:
            pass
        return None

    def write_txt_file(self, file_path, value):
        try:
            with open(file_path, 'w') as fd:
                fd.write(str(value))
                fd.flush()
        except IOError:
            return False
        return True

    def ipmi_raw(self, netfn, cmd):
        status = True
        result = ""
        try:
            cmd = "ipmitool raw {} {}".format(str(netfn), str(cmd))
            p = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            raw_data, err = p.communicate()
            if err == '':
                result = raw_data.strip()
            else:
                status = False
        except Exception:
            status = False
        return status, result

    def ipmi_fru_id(self, id, key=None):
        status = True
        result = ""
        try:
            cmd = "ipmitool fru print {}".format(str(
                id)) if not key else "ipmitool fru print {0} | grep '{1}' ".format(str(id), str(key))

            p = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            raw_data, err = p.communicate()
            if err == '':
                result = raw_data.strip()
            else:
                status = False
        except Exception:
            status = False
        return status, result

    def ipmi_set_ss_thres(self, id, threshold_key, value):
        status = True
        result = ""
        try:
            cmd = "ipmitool sensor thresh '{}' {} {}".format(str(id), str(threshold_key), str(value))
            p = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            raw_data, err = p.communicate()
            if err == '':
                result = raw_data.strip()
            else:
                status = False
        except Exception:
            status = False
        return status, result


class FileLock:

   def __init__(self, lock_file):
       self._lock_file = lock_file
       self._thread_lock = Lock()
       self.is_locked = False

   def acquire(self):
       with self._thread_lock:
           if self.is_locked:
               return

           fd = os.open(self._lock_file, flags=(os.O_RDWR | os.O_CREAT | os.O_TRUNC))
           fcntl.flock(fd, fcntl.LOCK_EX)
           self._lock_file_fd = fd
           self.is_locked = True

   def release(self):
       with self._thread_lock:
           if self.is_locked:
               fd = cast(int, self._lock_file_fd)
               self._lock_file_fd = None
               fcntl.flock(fd, fcntl.LOCK_UN)
               os.close(fd)
               self.is_locked = False

   def __enter__(self):
       self.acquire()
       return self

   def __exit__(self, exc_type, exc_val, traceback):
       self.release()

   def __del__(self):
       self.release()


DEVICE_THRESHOLD_JSON_PATH = "/tmp/device_threshold.json"
HIGH_THRESHOLD_FIELD = 'high_threshold'
LOW_THRESHOLD_FIELD = 'low_threshold'
HIGH_CRIT_THRESHOLD_FIELD = 'high_critical_threshold'
LOW_CRIT_THRESHOLD_FIELD = 'low_critical_threshold'
NOT_AVAILABLE = 'N/A'

class DeviceThreshold:

    def __init__(self, th_name = NOT_AVAILABLE):
        self.flock = FileLock("{}.lock".format(DEVICE_THRESHOLD_JSON_PATH))
        self.name = th_name
        self.__log = logger.Logger(log_identifier="DeviceThreshold")

        self.__db_data = {}
        try:
            with self.flock:
                with open(DEVICE_THRESHOLD_JSON_PATH, "r") as db_file:
                    self.__db_data = json.load(db_file)
        except Exception as e:
            self.__log.log_warning('{}'.format(str(e)))

    @property
    def HIGH_THRESHOLD_FIELD(self):
        return HIGH_THRESHOLD_FIELD

    @property
    def LOW_THRESHOLD_FIELD(self):
        return LOW_THRESHOLD_FIELD

    @property
    def HIGH_CRIT_THRESHOLD_FIELD(self):
        return HIGH_CRIT_THRESHOLD_FIELD

    @property
    def LOW_CRIT_THRESHOLD_FIELD(self):
        return LOW_CRIT_THRESHOLD_FIELD

    @property
    def NOT_AVAILABLE(self):
        return NOT_AVAILABLE

    def __get_data(self, field):
        """
        Retrieves data frome JSON file by field

        Args :
            field: String

        Returns:
            A string if getting is successfully, 'N/A' if not
        """
        if self.name not in self.__db_data.keys():
            return NOT_AVAILABLE

        if field not in self.__db_data[self.name].keys():
            return NOT_AVAILABLE

        return self.__db_data[self.name][field]

    def __set_data(self, field, new_val):
        """
        Set data to JSON file by field

        Args :
            field: String
            new_val: String 

        Returns:
            A boolean, True if setting is set successfully, False if not
        """
        if self.name not in self.__db_data.keys():
            self.__db_data[self.name] = {}

        old_val = self.__db_data[self.name].get(field, None)
        if old_val is not None and old_val == new_val:
            return True

        self.__db_data[self.name][field] = new_val

        try:
            with self.flock:
                db_data = {}
                mode = "w+"
                if os.path.exists(DEVICE_THRESHOLD_JSON_PATH):
                    mode = "r+"
                with open(DEVICE_THRESHOLD_JSON_PATH, mode) as db_file:
                    if mode == "r+":
                        db_data = json.load(db_file)

                    if self.name not in db_data.keys():
                        db_data[self.name] = {}

                    db_data[self.name][field] = new_val

                    if mode == "r+":
                        db_file.seek(0)
                        # erase old data
                        db_file.truncate(0)
                    # write all data
                    json.dump(db_data, db_file, indent=4)
        except Exception as e:
            self.__log.log_error('{}'.format(str(e)))
            return False

        return True

    def get_high_threshold(self):
        """
        Retrieves the high threshold temperature from JSON file.

        Returns:
            string : the high threshold temperature of thermal,
                     e.g. "30.125"
        """
        return self.__get_data(HIGH_THRESHOLD_FIELD)

    def set_high_threshold(self, temperature):
        """
        Sets the high threshold temperature of thermal
        Args :
            temperature: A string of temperature, e.g. "30.125"
        Returns:
            A boolean, True if threshold is set successfully, False if not
        """
        if isinstance(temperature, str) is not True:
            raise TypeError('The parameter requires string type.')

        try:
            if temperature != NOT_AVAILABLE:
                tmp = float(temperature)
        except ValueError:
            raise ValueError('The parameter requires a float string. ex:\"30.1\"')

        return self.__set_data(HIGH_THRESHOLD_FIELD, temperature)

    def get_low_threshold(self):
        """
        Retrieves the low threshold temperature from JSON file.

        Returns:
            string : the low threshold temperature of thermal,
                     e.g. "30.125"
        """
        return self.__get_data(LOW_THRESHOLD_FIELD)

    def set_low_threshold(self, temperature):
        """
        Sets the low threshold temperature of thermal
        Args :
            temperature: A string of temperature, e.g. "30.125"
        Returns:
            A boolean, True if threshold is set successfully, False if not
        """
        if isinstance(temperature, str) is not True:
            raise TypeError('The parameter requires string type.')

        try:
            if temperature != NOT_AVAILABLE:
                tmp = float(temperature)
        except ValueError:
            raise ValueError('The parameter requires a float string. ex:\"30.1\"')

        return self.__set_data(LOW_THRESHOLD_FIELD, temperature)

    def get_high_critical_threshold(self):
        """
        Retrieves the high critical threshold temperature from JSON file.

        Returns:
            string : the high critical threshold temperature of thermal,
                     e.g. "30.125"
        """
        return self.__get_data(HIGH_CRIT_THRESHOLD_FIELD)

    def set_high_critical_threshold(self, temperature):
        """
        Sets the high critical threshold temperature of thermal
        Args :
            temperature: A string of temperature, e.g. "30.125"
        Returns:
            A boolean, True if threshold is set successfully, False if not
        """
        if isinstance(temperature, str) is not True:
            raise TypeError('The parameter requires string type.')

        try:
            if temperature != NOT_AVAILABLE:
                tmp = float(temperature)
        except ValueError:
            raise ValueError('The parameter requires a float string. ex:\"30.1\"')

        return self.__set_data(HIGH_CRIT_THRESHOLD_FIELD, temperature)

    def get_low_critical_threshold(self):
        """
        Retrieves the low critical threshold temperature from JSON file.

        Returns:
            string : the low critical threshold temperature of thermal,
                     e.g. "30.125"
        """
        return self.__get_data(LOW_CRIT_THRESHOLD_FIELD)

    def set_low_critical_threshold(self, temperature):
        """
        Sets the low critical threshold temperature of thermal
        Args :
            temperature: A string of temperature, e.g. "30.125"
        Returns:
            A boolean, True if threshold is set successfully, False if not
        """
        if isinstance(temperature, str) is not True:
            raise TypeError('The parameter requires string type.')

        try:
            if temperature != NOT_AVAILABLE:
                tmp = float(temperature)
        except ValueError:
            raise ValueError('The parameter requires a float string. ex:\"30.1\"')

        return self.__set_data(LOW_CRIT_THRESHOLD_FIELD, temperature)

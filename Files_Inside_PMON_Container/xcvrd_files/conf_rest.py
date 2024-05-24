from flask import Flask
from flask_restplus import Resource, Api
from time import sleep

from sonic_platform_base.sonic_xcvr.codes.public.cmis import CmisCodes
from sonic_platform_base.sonic_xcvr.api.public.cmis import CmisApi
from sonic_platform_base.sonic_xcvr.api.public.c_cmis import CCmisApi
from sonic_platform_base.sonic_xcvr.mem_maps.public.cmis import CmisMemMap
from sonic_platform_base.sonic_xcvr.api.public.cmisVDM import CmisVdmApi

from sonic_platform_base.sonic_xcvr.codes.public.sff8436 import Sff8436Codes
from sonic_platform_base.sonic_xcvr.api.public.sff8436 import Sff8436Api
from sonic_platform_base.sonic_xcvr.mem_maps.public.sff8436 import Sff8436MemMap

from sonic_platform_base.sonic_xcvr.codes.public.sff8636 import Sff8636Codes
from sonic_platform_base.sonic_xcvr.api.public.sff8636 import Sff8636Api
from sonic_platform_base.sonic_xcvr.mem_maps.public.sff8636 import Sff8636MemMap

from sonic_platform_base.sonic_xcvr.codes.public.sff8472 import Sff8472Codes
from sonic_platform_base.sonic_xcvr.api.public.sff8472 import Sff8472Api
from sonic_platform_base.sonic_xcvr.mem_maps.public.sff8472 import Sff8472MemMap

from sonic_platform_base.sonic_xcvr.xcvr_eeprom import XcvrEeprom
from sonic_platform_base.sonic_xcvr.api.xcvr_api import XcvrApi

import sonic_platform.platform
import sonic_platform_base.sonic_sfp.sfputilhelper

import subprocess

interfaces = {
   1: "Ethernet0",
   2: "Ethernet4",
   3: "Ethernet8",
   4: "Ethernet12",
   5: "Ethernet16",
   6: "Ethernet20",
   7: "Ethernet24",
   8: "Ethernet28",
   9: "Ethernet32",
   10: "Ethernet36",
   11: "Ethernet40",
   12: "Ethernet44",
   13: "Ethernet48",
   14: "Ethernet53",
   15: "Ethernet56",
   16: "Ethernet60",
   17: "Ethernet64",
   18: "Ethernet68",
   19: "Ethernet72",
   20: "Ethernet76",
   21: "Ethernet80",
   22: "Ethernet84",
   23: "Ethernet88",
   24: "Ethernet96",
   25: "Ethernet100",
   26: "Ethernet104",
   27: "Ethernet108",
   28: "Ethernet112",
   29: "Ethernet116",
   30: "Ethernet120",
   31: "Ethernet124",
   32: "Ethernet128",
   33: "Ethernet132",
   34: "Ethernet136",
   35: "Ethernet140",
   36: "Ethernet144",
   37: "Ethernet148",
   38: "Ethernet152",
   39: "Ethernet156",
   40: "Ethernet160",
   41: "Ethernet164",
   42: "Ethernet168",
   43: "Ethernet172",
   44: "Ethernet176",
   45: "Ethernet180",
   46: "Ethernet184",
   47: "Ethernet188",
   48: "Ethernet192",
   49: "Ethernet196",
   50: "Ethernet200",
   51: "Ethernet204",
   52: "Ethernet208",
   53: "Ethernet212",
   54: "Ethernet216",
   55: "Ethernet220",
   56: "Ethernet224",
   57: "Ethernet228",
   58: "Ethernet232",
   59: "Ethernet236"
}

speeds = {
   "SPEED_10MB": 10,
   "SPEED_100MB": 100,
   "SPEED_1GB": 1000,
   "SPEED_250MB": 250,
   "SPEED_5GB": 5000,
   "SPEED_10GB": 10000,
   "SPEED_25GB": 25000,
   "SPEED_40GB": 40000,
   "SPEED_50GB": 50000,
   "SPEED_100GB": 100000,
   "SPEED_200GB": 200000,
   "SPEED_400GB": 400000,
   "SPEED_600GB": 600000,
   "SPEED_800GB": 800000
}

# Flask and Flask-RestPlus configuration
app = Flask(__name__)
api = Api(app, version='1.0', title='Sonic API',
          description='Rest API to setup Interface in Sonic devices. \nAuthor: Andrea Sgambelluri and Davide Scano')
sonic = api.namespace('Sonic', description='Sonic APIs')

#This REST implemnets all the commands used in https://github.com/Dscano/Testbed-OFC22/blob/main/Configs/Cnit54/config.md#config-port-192

@sonic.route('/Interface/<string:ip>/<int:mask>/<string:port>')
@sonic.response(200, 'Success')
@sonic.response(404, 'Error, not found')
class _config_Interface_IP(Resource):
    @sonic.doc(description="Interface IP configuration")
    @staticmethod
    def put(ip, mask, port):
            # str(interfaces[port])
            sudo_password = 'YourPaSsWoRd'
            command = 'config interface ip add ' + str(port) + ' ' + str(ip) + '/'+ str(mask)
            p = subprocess.Popen(['sudo', '-S'] + command.split(), stdin=subprocess.PIPE, stderr=subprocess.PIPE
                , universal_newlines=True)
            p.communicate(sudo_password + '\n')[1]
            print('Done!')
            return "OK", 200

    @sonic.doc(description="delete Interface IP configuration")
    @staticmethod
    def delete(ip, mask, port):            
            sudo_password = 'YourPaSsWoRd'
            command = 'config interface ip remove ' + str(port) + ' ' + str(ip) + '/'+ str(mask)
            p = subprocess.Popen(['sudo', '-S'] + command.split(), stdin=subprocess.PIPE, stderr=subprocess.PIPE
                , universal_newlines=True)
            p.communicate(sudo_password + '\n')[1]
            print('Done!')
            return "OK", 200

@sonic.route('/InterfaceConfig/<string:port>/<string:speed>')
@sonic.response(200, 'Success')
@sonic.response(404, 'Error, not found')
class _config_PORT(Resource):
    @sonic.doc(description="Interface UP configuration")
    @staticmethod
    def put(port, speed):
            sudo_password = 'YourPaSsWoRd'
            command = 'portconfig -p '+ str(port) + ' -s ' + str(speeds[speed])
            #subprocess.run(['portconfig', '-p' ,command])
            p = subprocess.Popen(['sudo', '-S'] + command.split(), stdin=subprocess.PIPE, stderr=subprocess.PIPE
                , universal_newlines=True)
            p.communicate(sudo_password + '\n')[1]
            print('Done!')
            return "OK", 200

@sonic.route('/TransceiverFreqConfig/<string:port>/<string:freq>/<string:grid>')
@sonic.response(200, 'Success')
@sonic.response(404, 'Error, not found')
class _Transceiver_config(Resource):
    @sonic.doc(description=" Transceiver frequency configuration")
    @staticmethod
    def put(port, freq, grid):
            sudo_password = 'YourPaSsWoRd'
            command = 'portconfig -p '+ str(port) + ' -F ' + str(freq) + ' -G ' + str(grid)
            print(command)
            #subprocess.run(['portconfig', '-p' ,command])
            p = subprocess.Popen(['sudo', '-S'] + command.split(), stdin=subprocess.PIPE, stderr=subprocess.PIPE
                , universal_newlines=True)
            p.communicate(sudo_password + '\n')[1]
            print('Done!')
            return "OK", 200

@sonic.route('/TransceiverPowerConfig/<string:port>/<string:power>')
@sonic.response(200, 'Success')
@sonic.response(404, 'Error, not found')
class _Transceiver_config(Resource):
    @sonic.doc(description=" Transceiver power configuration")
    @staticmethod
    def put(port, power):
            sudo_password = 'YourPaSsWoRd'
            command = 'portconfig -p '+ str(port) + ' -P ' + str(power)
            print(command)
            #subprocess.run(['portconfig', '-p' ,command])
            p = subprocess.Popen(['sudo', '-S'] + command.split(), stdin=subprocess.PIPE, stderr=subprocess.PIPE
                , universal_newlines=True)
            p.communicate(sudo_password + '\n')[1]
            print('Done!')
            return "OK", 200

@sonic.route('/TransceiverConfig/<int:port>')
@sonic.response(200, 'Success')
@sonic.response(404, 'Error, not found')
class _Transceiver_get(Resource):
    @sonic.doc(description=" Transceiver get measurements")
    @staticmethod
    def get(port):
        platform_chassis = sonic_platform.platform.Platform().get_chassis()
        platform_sfputil = sonic_platform_base.sonic_sfp.sfputilhelper.SfpUtilHelper()
        sfp = platform_chassis.get_sfp(port)
        api = sfp.get_xcvr_api()
        vals = CmisApi.get_vdm(api)
        freq = CCmisApi.get_laser_config_freq(api)
        grid = CCmisApi.get_freq_grid(api)
        power = CCmisApi.get_tx_power(api)
        ber = round(float(vals['Pre-FEC BER Current Value Media Input'][1][0]),6)
        osnr = round(float(vals['OSNR [dB]'][1][0]), 2)
        esnr = round(float(vals['eSNR [dB]'][1][0]),2)
        #return "RX={}\nBER={}\nOSNR={}\neSNR={}".format(rx, ber, osnr, esnr), 200
        return "FREQ={} \
                GRID={} \
                POWER={} \
                BER={} \
                OSNR={} \
                eSNR={}".format(freq, grid, power[1], ber, osnr, esnr), 200

        '''
        res = []
        res.append(vals['Pre-FEC BER Current Value Media Input'][1][0])
        res.append(vals['OSNR [dB]'][1][0])
        res.append(vals['eSNR [dB]'][1][0])
        return res, 200
        '''
@sonic.route('/InterfaceStatus/<string:portx>')
@sonic.response(200, 'Success')
@sonic.response(404, 'Error, not found')
class _config_Interface_UP(Resource):
    @sonic.doc(description="Activate Interface")
    @staticmethod
    def put(portx):
            sudo_password = 'YourPaSsWoRd'
            command = 'config interface startup ' + str(portx)
            p = subprocess.Popen(['sudo', '-S'] + command.split(), stdin=subprocess.PIPE, stderr=subprocess.PIPE
                , universal_newlines=True)
            p.communicate(sudo_password + '\n')[1]
            print('Done!')
            return "OK", 200

    @sonic.doc(description="Disable Interface")
    @staticmethod
    def delete(portx):
            sudo_password = 'YourPaSsWoRd'
            command = 'config interface shutdown ' + str(portx)
            p = subprocess.Popen(['sudo', '-S'] + command.split(), stdin=subprocess.PIPE, stderr=subprocess.PIPE
                , universal_newlines=True)
            p.communicate(sudo_password + '\n')[1]
            print('Done!')
            return "OK", 200
    
#BGP
#Implements https://github.com/Dscano/Testbed-OFC22/blob/main/Configs/Cnit54/config.md#bgp-configuration
@sonic.route('/bgp/<int:aut>/<int:remo_aut>/<string:ip>/<string:port>')
@sonic.response(200, 'Success')
@sonic.response(404, 'Error, not found')
class confBgpRoute(Resource):
    @sonic.doc(description="Configure BGP route")
    @staticmethod
    def put(aut,remo_aut,ip,port):
        if isinstance(aut,int):
             # str(interfaces[port])
            com1 = 'conf t \n router bgp ' + str(aut) + '\n neighbor '+ ip + ' remote-as ' + str(remo_aut)  \
            + ' \n neighbor '+ ip + ' interface ' + str(port) + '\n address-family ipv4 unicast' \
            + '\n neighbor '+ ip + ' activate'
            subprocess.run(['vtysh', '-c', com1])
            process = subprocess.run(['vtysh', '-c', 'write'], stdout=subprocess.PIPE)
            com2 = 'conf t \n router bgp ' + str(aut) + '\n address-family ipv4 unicast' \
            + ' \n redistribute static  \n redistribute connected'
            subprocess.run(['vtysh', '-c', com2])
            process = subprocess.run(['vtysh', '-c', 'write'], stdout=subprocess.PIPE)
            com3 = 'conf t \n access-list ALLOW_ALL permit any \n route-map PERMIT_ALL permit 1 \n match ip address ALLOW_ALL'
            subprocess.run(['vtysh', '-c', com3])
            process = subprocess.run(['vtysh', '-c', 'write'], stdout=subprocess.PIPE)
            if "saved" in str(process.stdout):
                return 'Success', 200
            else:
                return 'Success', 404

#Implements https://github.com/Dscano/Testbed-OFC22/blob/main/Configs/Cnit54/config.md#unconfigure-base-configuration
@sonic.route('/bgp/<int:aut>/')
@sonic.response(200, 'Success')
@sonic.response(404, 'Error, not found')
class removeBgpRoute(Resource):
    @sonic.doc(description="Remove BGP route")
    @staticmethod
    def delete(aut):
        if isinstance(aut,int):
            com1 = 'conf t \n no router bgp ' + str(aut) 
            subprocess.run(['vtysh', '-c', com1])
            process = subprocess.run(['vtysh', '-c', 'write'], stdout=subprocess.PIPE)
            com2 = 'conf t \n no route-map FROM_BGP_PEER_V6  \n no route-map FROM_BGP_PEER_V4 \n no route-map TO_BGP_PEER_V4' \
            '\n no route-map TO_BGP_PEER_V6 \n no route-map ALLOW_LIST_DEPLOYMENT_ID_0_V4 \n no route-map ALLOW_LIST_DEPLOYMENT_ID_0_V6'
            process = subprocess.run(['vtysh', '-c', 'write'], stdout=subprocess.PIPE)
            subprocess.run(['vtysh', '-c', com2])
            process2 = subprocess.run(['vtysh', '-c', 'write'], stdout=subprocess.PIPE)
            if ("saved" in str(process.stdout)) & ("saved" in str(process2.stdout)):
                return 'Success', 200
            else:
                return 'Success', 404

@sonic.route('/bgp/neighbor/<int:aut>/<int:remo_aut>/<string:ip>')
@sonic.response(200, 'Success')
@sonic.response(404, 'Error, not found')
class removeBgpNeighbor(Resource):
    @sonic.doc(description="Remove BGP neighbor")
    @staticmethod
    def delete(aut,remo_aut,ip):
        if isinstance(aut,int):
            # str(interfaces[port])
            com1 = 'conf t \n  router bgp ' + str(aut) + '\n no neighbor '+ ip + ' remote-as ' + str(remo_aut)
            subprocess.run(['vtysh', '-c', com1])
            process = subprocess.run(['vtysh', '-c', 'write'], stdout=subprocess.PIPE)
            if "saved" in str(process.stdout):
                return 'Success', 200
            else:
                return 'Success', 404


if __name__ == '__main__':
    #additional functions
    app.run(host='0.0.0.0', port=3005)


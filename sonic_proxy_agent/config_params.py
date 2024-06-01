try:
    import sonic_platform.platform
    import sonic_platform_base.sonic_sfp.sfputilhelper
    from .xcvrd_utilities import port_mapping
except ImportError as e:
    raise ImportError(str(e) + " - required module not found")

platform_chassis = None
phy_port = None
port_info_dict = None
dom_info_dict = None

def _init():
    platform_chassis = sonic_platform.platform.Platform().get_chassis()

def _get_phy_port():
    phy_port = input("Enter Physical Port:")
    print("Got the physical port as:", phy_port)

def _get_transceiver_info(physical_port):
		return platform_chassis.get_sfp(physical_port).get_transceiver_info()

def _get_transceiver_dom_info(physical_port):
    return platform_chassis.get_sfp(physical_port).get_transceiver_bulk_status()

def _get_tx_power():
    return dom_info_dict['txpower']


_init()
_get_phy_port()
port_info_dict = _get_transceiver_info(phy_port)
dom_info_dict = _get_transceiver_dom_info(phy_port)

print("Tx Power is:", _get_tx_power())

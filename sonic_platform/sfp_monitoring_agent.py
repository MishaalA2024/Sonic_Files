import time
from sonic_platform import Sfp  # Replace 'your_module' with the actual module name where the Sfp class is defined.

def check_transceiver(index):
    sfp = Sfp(index)  # Instantiate with a specific SFP index

    # Assuming 'get_transceiver_bulk_status' or a similar method returns a dictionary with temperature
    # Since the actual method to fetch temperature isn't specified, we will use a placeholder 'get_temperature'
    temperature = sfp.get_temperature()  # You need to replace 'get_temperature' with the actual method name
    temp_threshold = 70  # Define the temperature threshold

    # Check if the current temperature exceeds the threshold
    if temperature > temp_threshold:
        print(f"Temperature alert! SFP {index}: Current temperature {temperature}C exceeds threshold {temp_threshold}C.")

def main():
    # Example: Check temperatures for SFPs at indices 0 to 33
    while True:
        for index in range(34):
            check_transceiver(index)
        time.sleep(60)  # Check every 60 seconds

if __name__ == "__main__":
    main()

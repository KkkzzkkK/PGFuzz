"""
        Author: Hyungsub Kim
        Date: 05/20/2020
        Name of file: fuzzing.py
        Goal: Main loop of PGFUZZ for PX4
"""


import sys, os
from optparse import OptionParser

import time
import random
import threading
import subprocess

# Tell python where to find mavlink so we can import it
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), '../mavlink'))

from pymavlink import mavutil, mavwp
from pymavlink import mavextra
from pymavlink import mavexpression
import read_inputs
import shared_variables

import re
import math

#------------------------------------------------------------------------------------
# Global variables
# python /usr/local/bin/mavproxy.py --mav=/dev/tty.usbserial-DN01WM7R --baudrate 57600 --out udp:127.0.0.1:14540 --out udp:127.0.0.1:14550
connection_string = '0.0.0.0:14540'
master = mavutil.mavlink_connection(f'udp:{connection_string}')
home_altitude = 0
home_lat = 0
home_lon = 0
current_rc_3 = 0
takeoff = 0
drone_status = 0
wp_cnt = 0
lat = 0
lon = 0
# if 0: up / 1: down
goal_throttle = 0
executing_commands = 0
PARAM_MIN = 1
PARAM_MAX = 10000
required_min_thr = 975

current_roll = 0.0
current_pitch = 0.0
current_heading = 0.0

alt_error = 0.0
roll_error = 0.0
pitch_error = 0.0
heading_error = 0.0
stable_counter = 0

alt_series = 0.0
roll_series = 0.0
pitch_series = 0.0
heading_series = 0.0
position_cnt = 0
lat_series = 0.0
lon_series = 0.0

# states - 0: turn off, 1: turn on
Parachute_on = 0
Armed = 0
current_flight_mode = ""
previous_altitude = 0
current_altitude = 0
current_alt = 0.0
previous_alt = 0.0
GPS_status = 1
Accel_status = 1
Gyro_status = 1
Baro_status = 1
PreArm_error = 0
system_time = 0
mission_cnt = 0
num_GPS = 0
alt_GPS_series = 0.0
vertical_speed_series = 0.0
gps_message_cnt = 0

# Distance
P = []
Global_distance = 0
Previous_distance = []

target_param = ""
target_param_ready = 0
target_param_value = 0

REBOOT_START = 0

# A pair of input (input, value)
Current_input = ""
Current_input_val = ""
Guidance_decision = None
count_main_loop = 0

# Heartbeat
heartbeat_cnt = 1
RV_alive = 0

# Policy
Precondition_path = ""
#Current_policy = "PX.RTL1"
Current_policy = "PX.ALTITUDE1"
Current_policy_P_length = 4

base_mode = [157, 193, 209, 217]
flight_mode = ["manual", "stabilized", "acro", "rattitude", "altitude", "offboard", "position", "hold", "missition", "return", "follow_me"]

RTL_alt = 10

paramsName = []
target_roll = 1500
target_pitch = 1500
target_yaw = 1500

#------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------
# If the RV does not response within 5 seconds, we consider the RV's program crashed.
def check_liveness():
    global heartbeat_cnt

    end_flag = 0
    while end_flag == 0:

        if heartbeat_cnt < 1:
            store_mutated_inputs()
            with open("shared_variables.txt", "w") as f:
                f.write("reboot")
            end_flag = 1
        else:
            heartbeat_cnt = 0

        time.sleep(5)

# ------------------------------------------------------------------------------------
def set_preconditions(filepath):
    for line in open(filepath, 'r'):
        row = line.rstrip().split(' ')

        master.mav.param_set_send(master.target_system, master.target_component,
                                  row[0],
                                  float(row[1]),
                                  mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
        time.sleep(1)

        print(f"[Set_preconditions] {row[0]} = {row[1]}")

# ------------------------------------------------------------------------------------
def write_guidance_log(print_log, action):
    # Log mutated user command
    if action == "append":
        with open("guidance_log.txt", "a") as guidance_log:
            guidance_log.write(print_log)
        print(f"[write_guidance_log] appending: {print_log}")

    elif action == "write":
        guidance_log = open("guidance_log.txt", "w")
        guidance_log.close()

        with open("guidance_log.txt", "w") as guidance_log:
            guidance_log.write(print_log)
        print(f"[write_guidance_log] re-writing: {print_log}")

# ------------------------------------------------------------------------------------
def write_log(print_log):
    with open("mutated_log.txt", "a") as mutated_log:
        mutated_log.write(print_log)

# ------------------------------------------------------------------------------------
def re_launch():
    global goal_throttle
    global home_altitude
    global current_altitude
    global current_flight_mode
    global Parachute_on
    global count_main_loop
    global GPS_status
    global Accel_status
    global Gyro_status
    global Baro_status
    global PreArm_error
    global RV_alive

    RV_alive = 0
    count_main_loop = 0
    Parachute_on = 0
    GPS_status = 1
    Accel_status = 1
    Gyro_status = 1
    Baro_status = 1
    PreArm_error = 0

    print("#-----------------------------------------------------------------------------")
    print("#-----------------------------------------------------------------------------")
    print("#------------------------- RE-LAUNCH the vehicle -----------------------------")
    print("#-----------------------------------------------------------------------------")
    print("#-----------------------------------------------------------------------------")

    time.sleep(5)

    with open("shared_variables.txt", "w") as f:
        f.write("reboot")
    sys.exit()

# ------------------------------------------------------------------------------------
def verify_real_number(item):
    """ Method to find if an 'item'is real number"""

    item = str(item).strip()
    if not (item):
        return False
    elif (item.isdigit()):
        return True
    elif re.match(r"\d+\.*\d*", item) or re.match(r"-\d+\.*\d*", item):
        return True
    else:
        return False

# ------------------------------------------------------------------------------------
def change_parameter(selected_param):
    global Guidance_decision
    global Current_input
    global Current_input_val

    print(
        f"# [Change_parameter()] selected params: {read_inputs.param_name[selected_param]}"
    )

    param_name = read_inputs.param_name[selected_param]

    if Guidance_decision == True:
        Current_input_val = match_cmd(cmd=param_name)

    range_min = read_inputs.param_min[selected_param]
    range_max = read_inputs.param_max[selected_param]
    param_value = 0

    # Step 1. Check whether the selected parameter has an valid range or not
    if range_min == 'X':
        # Case 1-1: min (X) and max (O)
        if range_max != 'X':
            param_value = random.randint(PARAM_MIN, int(range_max))
            print("[param] selected params: %s, there is no min of valid range, random param value:%d" % (
            read_inputs.param_name[selected_param], param_value))

    elif range_max == 'X':
        param_value = random.randint(int(range_min), PARAM_MAX)
        print("[param] selected params: %s, there is no max of valid range, random param value:%d" % (
        read_inputs.param_name[selected_param], param_value))

    elif verify_real_number(range_min) == True:
        if range_min.isdigit() == True and range_max.isdigit() == True:
            param_value = random.randint(int(range_min), int(range_max))
            print("# [Change_parameter()] selected params: %s, min: %f, max: %f, random digit param value:%d" % (
            read_inputs.param_name[selected_param], float(range_min), float(range_max), param_value))

        elif range_min.isdigit() == False or range_max.isdigit() == False:
            param_value = random.uniform(float(range_min), float(range_max))
            print("# [Change_parameter()] selected params: %s, min: %f, max: %f, random real param value:%f" % (
            read_inputs.param_name[selected_param], float(range_min), float(range_max), param_value))

    # Step 2. Change the parameter value

    # 1) Request parameter
    global required_min_thr
    if param_name == "FS_THR_VALUE":
        param_value = random.randint(925, 975)
        required_min_thr = param_value
        print("# Required minimum throttle is %d" % param_value)

    if Current_input_val != "null":
        param_value = float(Current_input_val)
        print(f"@@@[Reuse stored input pair] ({param_name}, {Current_input_val})@@@")

    # 2) Set parameter value
    master.mav.param_set_send(master.target_system, master.target_component,
                              param_name,
                              param_value,
                              mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

    # Log change parameter values

    Current_input = param_name
    Current_input_val = str(param_value)

    print_param = "" + "P "
    print_param += param_name
    print_param += " "
    print_param += str(param_value)
    print_param += "\n"

    write_log(print_param)

    time.sleep(3)

# ------------------------------------------------------------------------------------
def cmd_set_home(home_location, altitude):
    #print '--- ', master.target_system, ',', master.target_component
    print("--- %d, %d", master.target_system, master.target_component)
    master.mav.command_long_send(
        master.target_system, master.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_HOME,
        1, # set position
        0, # param1
        0, # param2
        0, # param3
        0, # param4
        home_location[0], # lat
        home_location[1], # lon
        altitude) # alt

#------------------------------------------------------------------------------------
def read_params():

    global paramsName

    for line in open('default_params.txt', 'r'):
        row = line.rstrip().split(' ')
        paramsName.append(row[0])

	# For debug
	#print(paramsName)
		
#------------------------------------------------------------------------------------
def test_param():
    
    global paramsName
    global PARAM_MIN
    global PARAM_MAX

    for i in range(304, 604):
        if paramsName[i] in [
            "COM_POS_FS_EPH",
            "EKF2_ABL_LIM",
            "MC_PITCHRATE_FF",
            "MOT_SLEW_MAX",
            "MPC_THR_MAX",
            "SENS_BOARD_ROT",
        ]:
            continue
        """
        mav.mav.param_request_read_send(mav.target_system, mav.target_component, paramsName[i], -1)
        message = mav.recv_match(type='PARAM_VALUE', blocking=True)
        message = message.to_dict()
        print('(Original) name: %s\tvalue: %d' % (message['param_id'].decode("utf-8"), message['param_value']))
        """
        print("%s, %d" %(paramsName[i], PARAM_MIN))
        master.mav.param_set_send(master.target_system, master.target_component, paramsName[i], PARAM_MIN, mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

        time.sleep(3)


        print("%s:%d" % (paramsName[i], PARAM_MAX))
        master.mav.param_set_send(master.target_system, master.target_component, paramsName[i], PARAM_MAX, mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

        time.sleep(3)

        master.mav.param_set_send(master.target_system, master.target_component,
                                paramsName[i],
                                0,
                                mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
#------------------------------------------------------------------------------------
def change_flight_mode(mode):
    
    global base_mode

    if mode == "manual":
        main_mode = 1
        sub_mode = 0
    elif mode == "stabilized":
        main_mode = 7
        sub_mode = 0
    elif mode == "acro":
        main_mode = 5
        sub_mode = 0
    elif mode == "rattitude":
        main_mode = 8
        sub_mode = 0
    elif mode == "altitude":
        main_mode = 2
        sub_mode = 0
    elif mode == "offboard":
        main_mode = 6
        sub_mode = 0
    elif mode == "position":
        main_mode = 3
        sub_mode = 0
    elif mode == "hold":
        main_mode = 4
        sub_mode = 3
    elif mode == "missition":
        main_mode = 4
        sub_mode = 4
    elif mode == "return":
        main_mode = 4
        sub_mode = 5
    elif mode == "follow_me":
        main_mode = 4
        sub_mode = 8

    for i in range (0, 4):
        # Debug
        # print("[DEBUG] i:%d, base_mode:%d, main_mode:%d, sub_mode:%d" %(i, base_mode[i], main_mode, sub_mode))

        master.mav.command_long_send(
            1, 1,
            mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
            base_mode[i], main_mode, sub_mode, 0, 0, 0, 0)

        time.sleep(1)


#------------------------------------------------------------------------------------	
# --------------------- (Start) READ Robotic Vehicle's states ------------------------
def handle_heartbeat(msg):
    global heartbeat_cnt
    heartbeat_cnt += 1

    global current_flight_mode

    if mavutil.mode_string_v10(msg) != "Mode(0x000000c0)":
        current_flight_mode = mavutil.mode_string_v10(msg)

    global drone_status
    drone_status = msg.system_status
    #print("[DEBUG] Drone status: %d, mavlink version: %d" % (drone_status, msg.mavlink_version))

    is_armed = msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
    is_enabled = msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_GUIDED_ENABLED

    # print("[DEBUG] Mode: %s, is_armed: %d, is_enabled: %d" % (current_flight_mode, is_armed, is_enabled))

#------------------------------------------------------------------------------------
def handle_rc_raw(msg):
    global current_rc_3
    current_rc_3 = msg.chan3_raw

    channels = (msg.chan1_raw, msg.chan2_raw, msg.chan3_raw, msg.chan4_raw,
                msg.chan5_raw, msg.chan6_raw, msg.chan7_raw, msg.chan8_raw)

#------------------------------------------------------------------------------------
def handle_hud(msg):
    global current_yaw
    global alt_series
    global  stable_counter

    hud_data = (msg.airspeed, msg.groundspeed, msg.heading,
                msg.throttle, msg.alt, msg.climb)

    # print "Aspd\tGspd\tHead\tThro\tAlt\tClimb"
    # print "%0.2f\t%0.2f\t%0.2f\t%0.2f\t%0.2f\t%0.2f" % hud_data

    # print("Alt: %f" %msg.alt)

    global current_altitude
    global previous_altitude
    global current_heading

    previous_altitude = current_altitude
    current_altitude = msg.alt

    alt_series += current_altitude
    stable_counter += 1
    #print("[DEBUG] previous_altitude:%f, current_altitude:%f" % (current_altitude, previous_altitude))

    current_heading = (msg.heading - 359)

#------------------------------------------------------------------------------------
def handle_attitude(msg):
    global current_roll
    global current_pitch

    attitude_data = (msg.roll, msg.pitch, msg.yaw, msg.rollspeed,
                     msg.pitchspeed, msg.yawspeed)
    # print "Roll\tPit\tYaw\tRSpd\tPSpd\tYSpd"
    # print "%0.6f\t%0.6f\t%0.2f\t%0.2f\t%0.2f\t%0.2f\t" % attitude_data

    current_roll = (msg.roll * 180) / math.pi
    current_pitch = (msg.pitch * 180) / math.pi

#------------------------------------------------------------------------------------
def handle_target(msg):
    global current_roll
    global current_pitch
    global current_altitude
    global alt_error
    global roll_error
    global pitch_error
    global heading_error

    global alt_series
    global roll_series
    global pitch_series
    global heading_series
    global stable_counter

    # print("altitude error:%f" %msg.alt_error)
    # msg.alt_error
    """
	nav_roll	float	deg	Current desired roll
	nav_pitch	float	deg	Current desired pitch
	nav_bearing	int16_t	deg	Current desired heading
	target_bearing	int16_t	deg	Bearing to current waypoint/target
	wp_dist	uint16_t	m	Distance to active waypoint
	alt_error	float	m	Current altitude error
	aspd_error	float	m/s	Current airspeed error
	xtrack_error	float	m	Current crosstrack error on x-y plane
	"""
    reference = (msg.nav_roll, msg.nav_pitch, msg.nav_bearing, msg.alt_error, msg.aspd_error, msg.xtrack_error)
    # print "\nRF_Roll\tRF_Pitch\tRF_Head\tRF_Alt\tRF_Spd\tRF_XY"
    # print "%0.2f\t%0.2f\t%0.2f\t%0.2f\t%0.2f\t%0.2f\t" % reference

    # print("\nRoll error:%f, pitch error:%f, heading error:%f\n" %(abs(msg.nav_roll - current_roll), abs(msg.nav_pitch - current_pitch), abs(msg.nav_bearing - current_heading)))

    alt_error += msg.alt_error
    roll_error += abs(msg.nav_roll - current_roll)
    pitch_error += abs(msg.nav_pitch - current_pitch)
    heading_error += abs(msg.nav_bearing - current_heading)
    stable_counter += 1

    # Compute moving average of each state
    alt_series += (current_altitude + msg.alt_error)
    roll_series += msg.nav_roll
    pitch_series += msg.nav_pitch
    heading_series += msg.nav_bearing

    #print("[DEBUG] current_altitude:%f, alt_error:%f" %(current_altitude, msg.alt_error))
    #print("Desired heading:%f, current heading:%f" %(msg.nav_bearing, current_heading))

#------------------------------------------------------------------------------------
def handle_param(msg):
    global target_param
    global target_param_ready
    global target_param_value

    message = msg.to_dict()
    if message['param_id'].decode("utf-8") == target_param:
        target_param_ready = 1
        target_param_value = message['param_value']
    else:
        target_param_ready = 0

#------------------------------------------------------------------------------------
def handle_position(msg):
    position_data = (msg.lat, msg.lon)

    # print(msg)
    # print("lat:%f, lon:%f" %(float(msg.lat), float(msg.lon)))

    global position_cnt
    global lat_series
    global lon_series

    position_cnt += 1
    lat_series += msg.lat
    lon_series += msg.lon

# ------------------------------------------------------------------------------------
def handle_status(msg):
    global Parachute_on
    global GPS_status
    global Accel_status
    global Gyro_status
    global PreArm_error
    global Baro_status
    global drone_status

    status_data = (msg.severity, msg.text)
    print(f"[status_text] {msg.text}")

    # Detecting a depolyed parachute
    if "Parachute: Released" in msg.text:
        Parachute_on = 1

    # Detecting an error on GPS
    elif "NavEKF" in msg.text and "lane switch" in msg.text:
        GPS_status = 0

    # Detecting an error on gyro sensor
    elif "Vibration compensation ON" in msg.text:
        Gyro_status = 0

    # Detecting an error on accelerometer sensor
    elif "EKF primary changed" in msg.text:
        Accel_status = 0

    # Detecting error messages related to a barometer sensor
    elif "PreArm: Waiting for Nav Checks" in msg.text:
        Baro_status = 0

    # Detecting a PreArm check error
    elif "PreArm: Check" in msg.text:
        PreArm_error = 1

    # Detecting landing on the ground
    elif "Landing detected" in msg.text:
        drone_status = 3 # 3: The drone is landed on the ground
        print("[DEBUG] The drone is landed on the ground!")

    # ------------------------------------------------------------------------------------
def handle_time(msg):
    global system_time

    # time_data = (msg.time_unix_usec, msg.time_boot_ms)
    system_time = msg.time_unix_usec

    # print "[system_time] UNIX time:%d, Boot time:%d" % time_data

# ------------------------------------------------------------------------------------
def handle_mission(msg):
    global mission_cnt

    mission_cnt = msg.count
    print("[MISSION_COUNT]%d" % mission_cnt)

# ------------------------------------------------------------------------------------
def handle_gps(msg):
    global num_GPS
    global alt_GPS_series
    global gps_message_cnt

    gps_message_cnt += 1
    num_GPS = int(msg.satellites_visible)
    alt_GPS_series += int(msg.alt / 1000)  # convert mm unit to meter unit

    # print("# of GPS:%d, GPS altitude:%d"%(num_GPS, alt_GPS))

# ------------------------------------------------------------------------------------
def read_loop():
    while True:
        # grab a mavlink message
        msg = master.recv_match(blocking=True)

        # handle the message based on its type
        msg_type = msg.get_type()
        if msg_type == "BAD_DATA":
            if mavutil.all_printable(msg.data):
                sys.stdout.write(msg.data)
                sys.stdout.flush()
        elif msg_type == "RC_CHANNELS":
            handle_rc_raw(msg)
        elif msg_type == "VFR_HUD":
            handle_hud(msg)
        elif msg_type == "ATTITUDE":
            handle_attitude(msg)
        elif msg_type == "NAV_CONTROLLER_OUTPUT":
            handle_target(msg)
        elif msg_type == "GLOBAL_POSITION_INT":
            handle_position(msg)
        elif msg_type == "STATUSTEXT":
            handle_status(msg)
        elif msg_type == "SYSTEM_TIME":
            handle_time(msg)
        elif msg_type == "MISSION_COUNT":
            handle_mission(msg)
        elif msg_type == "PARAM_VALUE":
            handle_param(msg)
        elif msg_type == "GPS_RAW_INT":
            handle_gps(msg)
        elif msg_type == "HEARTBEAT":
            handle_heartbeat(msg)


# --------------------- (End) READ Robotic Vehicle's states ------------------------
# ------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------
def store_mutated_inputs():

    for _ in range(3):
        print("***************Policy violation!***************")

    with open("mutated_log.txt", "r") as f1:
        lines = f1.readlines()

        f_itr = open("iteration.txt", "r")

        file_name = "" + "./policy_violations/"
        file_name += f_itr.read()
        file_name += ".txt"

        f2 = open(file_name, "w")
        f2.writelines(lines)
    f2.close()
    f_itr.close()

    mutated_log = open("mutated_log.txt", "w")
    mutated_log.close()

    # If we detect a policy violation, let's restart the simulator
    re_launch()

# ------------------------------------------------------------------------------------
def print_distance(G_dist, P_dist, length, policy, guid):
    # Print distances
    print(
        f"#---------------------------------{policy}-------------------------------------------"
    )
    sys.stdout.write("[Distance] ")
    for i in range(length):
        sys.stdout.write("P%d: %f " % (i + 1, P_dist[i]))

    print("")
    print('[Distance] Global distance: %f' % Global_distance)
    print("#-----------------------------------------------------------------------------")

    if G_dist < 0:
        store_mutated_inputs()

    global Current_policy_P_length
    global Previous_distance

    if policy == Current_policy:
        # Previous distances
        if guid == "false":
            for i in range(Current_policy_P_length):
                Previous_distance[i] = P_dist[i]

        elif guid == "true":
            global Current_input
            global Current_input_val

            for i in range(Current_policy_P_length):

                log_flag = -1
                guide_line = ""
                fp = open("guidance_log.txt", 'r')

                if Previous_distance[i] < P_dist[i]:
                    log_flag = 0
                    # a) Checking whether there is already stored same input pair
                    for line in fp:
                        guide_line += line

                        if Current_input in line:
                            row = line.rstrip().split(' ')
                            if int(row[2]) == i + 1:
                                log_flag = 1
                                print(f"[Redundant input] {row[0]} {row[1]} {row[2]} {row[3]}")
                                print("[Redundant input] old:%f - new:%f" % (
                                float(row[3]), (P_dist[i] - Previous_distance[i])))
                                if float(row[3]) <= (P_dist[i] - Previous_distance[i]):
                                    log_flag = 2
                                    print_input = ""
                                    print_input += Current_input  # 0
                                    print_input += " "
                                    print_input += Current_input_val  # 1
                                    print_input += " "
                                    print_input += str(i + 1)  # 2
                                    print_input += " "
                                    print_input += str(P_dist[i] - Previous_distance[i])  # 3
                                    print_input += "\n"
                                    guide_line = guide_line.replace(line, print_input)
                                    print(
                                        "[Redundant input] we need to log %s because it increase more propositional distance %d" % (
                                        Current_input, i + 1))
                # Append a new input
                if log_flag == 0:
                    print("[*Distance*] propositional distance %d is increased (input: %s, %s)" % (
                    i + 1, Current_input, Current_input_val))

                    print_input = ""
                    print_input += Current_input
                    print_input += " "
                    print_input += Current_input_val
                    print_input += " "
                    print_input += str(i + 1)
                    print_input += " "
                    print_input += str(P_dist[i] - Previous_distance[i])
                    print_input += "\n"
                    fp.close()
                    write_guidance_log(print_input, action="append")

                elif log_flag in [1, -1]:
                    fp.close()

                elif log_flag == 2:
                    fp.close()
                    write_guidance_log(guide_line, action="write")


# ------------------------------------------------------------------------------------
# ---------------(Start) Calculate propositional and global distances-----------------
def calculate_distance(guidance):
    # State global variables
    global alt_series
    global roll_series
    global pitch_series
    global heading_series
    global stable_counter

    global lat_series
    global lon_series
    global position_cnt
    global home_lat
    global home_lon

    global current_alt
    global previous_alt
    global Previous_distance

    global num_GPS
    global alt_GPS_series
    global gps_message_cnt

    if stable_counter > 0:
        alt_avg = alt_series / stable_counter
        roll_avg = roll_series / stable_counter
        pitch_avg = pitch_series / stable_counter
        heading_avg = heading_series / stable_counter
    else:
        alt_avg = 0
        roll_avg = 0
        pitch_avg = 0
        heading_avg = 0

    if position_cnt > 0:
        lat_avg = lat_series / position_cnt
        lon_avg = lon_series / position_cnt

    else:
        lat_avg = 0
        lon_avg = 0

    lat_avg = lat_avg / 1000
    lat_avg = lat_avg * 1000
    lon_avg = lon_avg / 1000
    lon_avg = lon_avg * 1000

    previous_alt = current_alt
    current_alt = math.ceil(alt_avg)

    # Calculate a relative altitude from the home postion
    current_alt = current_alt - home_altitude

    if gps_message_cnt > 0:
        alt_GPS_avg = alt_GPS_series / gps_message_cnt
    else:
        alt_GPS_avg = alt_GPS_series

    # print('[Debug] stable_counter:%d' %stable_counter)
    # print('[Debug] alt_avg:%f (previous_alt:%f, current_alt:%f), roll_avg:%f, pitch_avg:%f, heading_avg:%f' %(alt_avg, previous_alt, current_alt, roll_avg, pitch_avg, heading_avg))
    print('[Debug] lat_avg:%f, home_lat:%f, lon_avg:%f, home_lon:%f' % (lat_avg, home_lat, lon_avg, home_lon))

    position_cnt = 0
    stable_counter = 0
    alt_series = 0
    roll_series = 0
    pitch_series = 0
    heading_series = 0
    lat_series = 0
    lon_series = 0
    alt_GPS_series = 0
    gps_message_cnt = 0

    global target_param
    global target_param_ready
    global target_param_value

    global Parachute_on
    global Armed
    global current_flight_mode
    global previous_altitude
    global current_altitude
    global goal_throttle

    global P
    global Global_distance

    target_param_ready = 0
    # ----------------------- (start) PX.CHUTE1 policy -----------------------
    # ----------------------- (start) PX.CHUTE1 policy -----------------------
    # Propositional distances
    # 0: turn off, 1: turn on

    # P1
    P[0] = 1 if Parachute_on == 1 else -1
    # P2
    P[1] = 1 if Armed == 0 else -1
    # P3
    P[2] = 1 if current_flight_mode in ["FLIP", "ACRO"] else -1
    # P4
    P[3] = (current_alt - previous_alt) / current_alt if current_alt > 0 else 0
    # P5
    # Request parameter
    master.mav.param_request_read_send(master.target_system, master.target_component, 'CHUTE_ALT_MIN', -1)

    target_param = "CHUTE_ALT_MIN"
    count = 0
    while target_param_ready == 0 and count < 5:
        time.sleep(1)
        count += 1

    if target_param_value > 0:
        P[4] = (target_param_value - current_alt) / target_param_value
    else:
        P[4] = 0

    Global_distance = -1 * (min(P[0], max(P[1], P[2], P[3], P[4])))

    print_distance(G_dist=Global_distance, P_dist=P, length=5, policy="PX.CHUTE", guid=guidance)

    # ----------------------- (end) PX.CHUTE1 policy -----------------------
    # ----------------------- (end) PX.CHUTE1 policy -----------------------

    target_param_ready = 0
    # ----------------------- (start) PX.RTL1 policy -----------------------
    # ----------------------- (start) PX.RTL1 policy -----------------------
    # P1
    P[0] = -1 if lat_avg == home_lat and lon_avg == home_lon else 1
    # P2
    # Request parameter
    master.mav.param_request_read_send(
        master.target_system, master.target_component, 'RTL_ALT', -1)

    target_param = "RTL_ALT"
    count = 0
    while target_param_ready == 0 and count < 5:
        time.sleep(1)
        count += 1

    # Convert centimeters to meters
    target_param_value = target_param_value / 100

    if target_param_value > 0:
        P[1] = (target_param_value - current_alt) / target_param_value
    else:
        P[1] = 0

    print("[Debug] RTL_ALT:%f, current_alt:%f" % (target_param_value, current_alt))

    P[2] = 1 if current_flight_mode == "RTL" else -1
    P[3] = (previous_alt - current_alt) / previous_alt if previous_alt != 0 else 0
    Global_distance = -1 * (min(P[0], P[1], P[2], P[3]))

    print_distance(G_dist=Global_distance, P_dist=P, length=4, policy="PX.RTL1", guid=guidance)
    # ----------------------- (end) PX.RTL1 policy -----------------------
    # ----------------------- (end) PX.RTL1 policy -----------------------

    target_param_ready = 0
    # ----------------------- (start) PX.ALTITUDE1 policy -----------------------
    # ----------------------- (start) PX.ALTITUDE1 policy -----------------------
    # Propositional distances
    # 0: turn off, 1: turn on

    #print("[DEBUG] Mode:%s, Throttle:%d, Alt_t:%f, Alt_prev:%f" %(current_flight_mode, goal_throttle, current_alt, previous_alt))

    # P1: Mode_t = ALTITUDE
    P[0] = 1 if current_flight_mode == "ALTCTL" else -1
    # P2: Throttle_t = 1500
    P[1] = 1 if goal_throttle == 1500 else -1
    # P3: ALT_t = ALT_(t-1)
    P[2] = -1 if current_alt == previous_alt else 1
    Global_distance = -1 * (min(P[0], P[1], P[2]))

    print_distance(G_dist=Global_distance, P_dist=P, length=3, policy="PX.ALTITUDE", guid=guidance)

    # ----------------------- (end) PX.ALTITUDE1 policy -----------------------
    # ----------------------- (end) PX.ALTITUDE1 policy -----------------------

    target_param_ready = 0
    target_param = ""
    target_param_value = 0
    """
	global Current_policy_P_length
	# previous distances
	if guidance == "false":
		for i in range(Current_policy_P_length):
			Previous_distance[i] = P[i]
	elif guidance == "true":
		global Current_input
		global Current_input_val
		for i in range(Current_policy_P_length):
	                log_flag = -1
	                guide_line = ""
	                fp = open("guidance_log.txt", 'r')
			if Previous_distance[i] < P[i]:
				log_flag = 0
				# a) Checking whether there is already stored same input pair
				for line in fp.readlines():
					guide_line += line
                    			if Current_input in line:
                        			row = line.rstrip().split(' ')
						if int(row[2]) == i+1:
							log_flag = 1
                        				print("[Redundant input] {} {} {} {}".format(row[0], row[1], row[2], row[3]))
							print("[Redundant input] old:%f - new:%f" %(float(row[3]), (P[i]-Previous_distance[i])))                        
                        				if float(row[3]) <= (P[i]-Previous_distance[i]):
                            					log_flag = 2
								print_input = ""
	                    					print_input += Current_input #0
						                print_input += " "
						                print_input += Current_input_val #1
								print_input += " "
                                                                print_input += str(i+1) #2
					                        print_input += " "
						                print_input += str(P[i]-Previous_distance[i]) #3
						                print_input += "\n"
								guide_line = guide_line.replace(line, print_input)
                	            				print("[Redundant input] we need to log %s because it increase more propositional distance %d" %(Current_input, i+1))
                	# Append a new input        
                	if log_flag == 0:
                    		print("[*Distance*] propositional distance %d is increased (input: %s, %s)" % (i+1, Current_input, Current_input_val))
                    		print_input = ""
                    		print_input += Current_input
                    		print_input += " "
                    		print_input += Current_input_val
                    		print_input += " "
				print_input += str(i+1)
                                print_input += " "
                    		print_input += str(P[i]-Previous_distance[i])
                    		print_input += "\n"
				fp.close()
				write_guidance_log(print_input, action = "append")
			elif log_flag == 1 or log_flag == -1:
				fp.close()
			elif log_flag == 2:
				fp.close()
				write_guidance_log(guide_line, action = "write")
		"""


# ------------------------------------------------------------------------------------
# Create a function to send RC values
# More information about Joystick channels
# here: https://www.ardusub.com/operators-manual/rc-input-and-output.html#rc-inputs
def set_rc_channel_pwm(id, pwm=1500):
    """ Set RC channel pwm value
    Args:
        id (TYPE): Channel ID
        pwm (int, optional): Channel pwm value 1100-1900
    """
    if id < 1:
        print("Channel does not exist.")
        return

    # We only have 8 channels
    # https://mavlink.io/en/messages/common.html#RC_CHANNELS_OVERRIDE
    if id < 9:
        rc_channel_values = [65535 for _ in range(8)]
        rc_channel_values[id - 1] = pwm

        # global master

        master.mav.rc_channels_override_send(
            master.target_system,  # target_system
            master.target_component,  # target_component
            *rc_channel_values)  # RC channel list, in microseconds.


# ------------------------------------------------------------------------------------
def throttle_th():
    global goal_throttle

    while True:
        set_rc_channel_pwm(3, goal_throttle)
        time.sleep(0.2)

# ------------------------------------------------------------------------------------
def match_cmd(cmd):
    cmds = []
    with open("guidance_log.txt", 'r') as f:
        cmds.extend(line for line in f if cmd in line)
    if len(cmds) >= 2:
        index = random.randint(0, len(cmds) - 1)
        row = cmds[index].rstrip().split(' ')
        print("*****")
        print(f"[Matched input] {row[0]} {row[1]} {row[2]} {row[3]}")
        print("*****")

        return row[1]

    elif len(cmds) == 1:
        row = cmds[0].rstrip().split(' ')

        print("*****")
        print(f"[Matched input] {row[0]} {row[1]} {row[2]} {row[3]}")
        print("*****")

        return row[1]

    return "null"

# ------------------------------------------------------------------------------------
def execute_cmd(num):
    global Current_input
    global Current_input_val
    global Guidance_decision
    rand = [random.randint(1, 100) for _ in range(7)]
    # To do: implement all if statements for all user commands

    Current_input = read_inputs.cmd_name[num]

    if Guidance_decision == True:
        Current_input_val = match_cmd(cmd=Current_input)

    if Current_input_val != "null":
        print(
            f"@@@[Reuse stored input pair] ({Current_input}, {Current_input_val})@@@"
        )

    # ------------------------(start) execute a selected command-------------------------
    if read_inputs.cmd_name[num] in ["RC1", "RC2", "RC4"]:
        target_RC = 0
        if Current_input_val == "null":
            mutated_value = random.randint(1200, 1900)
            Current_input_val = str(mutated_value)
        else:
            mutated_value = int(Current_input_val)

        if read_inputs.cmd_name[num] == "RC1":
            target_RC = 1
        elif read_inputs.cmd_name[num] == "RC2":
            target_RC = 2
        elif read_inputs.cmd_name[num] == "RC4":
            target_RC = 4

        set_rc_channel_pwm(target_RC, mutated_value)

    elif read_inputs.cmd_name[num] == "RC3":
        global goal_throttle

        if Current_input_val == "null":
            goal_throttle = random.randint(1000, 2000)
            Current_input_val = str(goal_throttle)
        else:
            goal_throttle = int(Current_input_val)

    elif read_inputs.cmd_name[num] == "Flight_Mode":
        # Triggering a proper flight mode makes PGFUZZ quickly testing each policy
        Current_input_val = read_inputs.cmd_number[num]

        if Current_input_val == "null":
            rand_fligh_mode = random.randint(0, 18)
            Current_input_val = str(rand_fligh_mode)
        else:
            rand_fligh_mode = int(Current_input_val)

        master.mav.set_mode_send(
            master.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            rand_fligh_mode)

    elif read_inputs.cmd_name[num] == "MAV_CMD_DO_PARACHUTE":
        Current_input_val = "2"

        master.mav.command_long_send(
            master.target_system,  # target_system
            master.target_component,  # target_component
            mavutil.mavlink.MAV_CMD_DO_PARACHUTE,
            0, 2, 0, 0, 0, 0, 0, 0)
    else:
        if Current_input_val != "null" and "," in Current_input_val:
            row = Current_input_val.rstrip().split(',')
            for i in range(7):
                rand[i] = int(row[i])

        Current_input_val = f"{str(rand[0])},"
        Current_input_val += str(rand[1])
        Current_input_val += ","
        Current_input_val += str(rand[2])
        Current_input_val += ","
        Current_input_val += str(rand[3])
        Current_input_val += ","
        Current_input_val += str(rand[4])
        Current_input_val += ","
        Current_input_val += str(rand[5])
        Current_input_val += ","
        Current_input_val += str(rand[6])

        master.mav.command_long_send(
            master.target_system,  # target_system
            master.target_component,  # target_component
            int(read_inputs.cmd_number[num]),
            0,
            rand[0], rand[1], rand[2], rand[3], rand[4], rand[5], rand[6])
    # ------------------------(end) execute a selected command-------------------------

    print(f"[Execute_cmd] ({Current_input}, {Current_input_val})")

    print_cmd = "" + "C "
    print_cmd += Current_input
    print_cmd += " "
    print_cmd += Current_input_val
    print_cmd += "\n"
    write_log(print_cmd)

# ------------------------------------------------------------------------------------
def execute_env(num):
    global Current_input
    global Current_input_val
    global Guidance_decision

    # To do: implement all if statements for all user commands

    Current_input = read_inputs.env_name[num]

    if Guidance_decision == True:
        Current_input_val = match_cmd(cmd=Current_input)

    if Current_input_val == "null":
        rand = random.uniform(0, 100)
        Current_input_val = str(rand)
    else:
        print(
            f"@@@[Reuse stored input pair] ({Current_input}, {Current_input_val})@@@"
        )

    master.mav.param_set_send(master.target_system, master.target_component,
                              Current_input,
                              float(Current_input_val),
                              mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

    print(f"[Execute_env] ({Current_input}, {Current_input_val})")

    print_env = "" + "E "
    print_env += Current_input
    print_env += " "
    print_env += Current_input_val
    print_env += "\n"
    write_log(print_env)

# ------------------------------------------------------------------------------------
def pick_up_cmd():
    global Current_input
    global Current_input_val
    global Guidance_decision
    global RV_alive

    RV_alive = 1

    Current_input = ""
    Current_input_val = "null"
    Guidance_decision = None

    # a) Randomly select a type of inputs ( 1)user command, 2)parameter, 3)environmental factor)
    input_type = random.randint(1, 3)

    # Hyungsub - to test user commands! I need to remove the below code after finishing to implement all user commands
    # input_type = 1

    # True: input mutated from guidance, False: randomly mutate an input
    Guidance_decision = random.choice([True, False])

    # b) Randomly select an input from the selected type of inputs

    # 1) User commands
    if input_type == 1:
        execute_cmd(num=random.randint(0, len(read_inputs.cmd_name) - 1))

    # 2) Parameters
    elif input_type == 2:
        change_parameter(selected_param=random.randint(0, len(read_inputs.param_name) - 1))

    # 3) Environmental factors
    elif input_type == 3:
        execute_env(num=random.randint(0, len(read_inputs.env_name) - 1))

# ------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------
# Parsing parameters
# To do: update the below path according to a changed target policy
f_path_def = ""
f_path_def += "./policies/"
f_path_def += Current_policy

print("#-----------------------------------------------------------------------------")
params_path = ""
params_path += f_path_def
params_path += "/parameters.txt"
read_inputs.parsing_parameter(params_path)
print("# Check whether parsing parameters well done or not, received # of params: %d" % len(read_inputs.param_name))
print(read_inputs.param_name)

cmd_path = ""
cmd_path += f_path_def
cmd_path += "/cmds.txt"

read_inputs.parsing_command(cmd_path)
print("# Check whether parsing user commands well done or not, received # of params: %d" % len(read_inputs.cmd_name))
print(read_inputs.cmd_name)

env_path = ""
env_path += f_path_def
env_path += "/envs.txt"

read_inputs.parsing_env(env_path)
print("# Check whether parsing environmental factors well done or not, received # of params: %d" % len(
    read_inputs.env_name))
print(read_inputs.env_name)
print("#-----------------------------------------------------------------------------")

#------------------------------------------------------------------------------------

# (Start) Parse command line arguments (i.e., input and output file)
f_input_type = open("input_mutation_type.txt", "r")
input_type = f_input_type.readline()
if input_type == 'true':
    print("[DEBUG] User chooses bounded input mutation")
    PARAM_MIN = 1
    PARAM_MAX = 10000
else:
    print("[DEBUG] User chooses unbounded input mutation")
    PARAM_MIN = 1
    PARAM_MAX = 999999999999
# (End) Parse command line arguments (i.e., input and output file)

master.wait_heartbeat()

# request data to be sent at the given rate
"""
for i in range(0, 3):
    master.mav.request_data_stream_send(master.target_system, master.target_component,
                                        mavutil.mavlink.MAV_DATA_STREAM_ALL, 6, 1)
"""
message = master.recv_match(type='VFR_HUD', blocking=True)
home_altitude = message.alt
print("home_altitude: %f" % home_altitude)

message = master.recv_match(type='GLOBAL_POSITION_INT', blocking=True)
home_lat = message.lat
home_lat = home_lat / 1000
home_lat = home_lat * 1000
home_lon = message.lon
home_lon = home_lon / 1000
home_lon = home_lon * 1000
print("home_lat: %f, home_lon: %f" % (home_lat, home_lon))

# Testing --------------------------------------------------------------------------------------
for i in range(30):
    P.append(0)
    Previous_distance.append(0)

waypoints = [
        (35.5090904347, 127.045094298),
        (35.509070898, 127.048905867),
        (35.5063678607, 127.048960654),
        (35.5061713129, 127.044741936),
        (35.5078823794, 127.046914506)
        ]

wp = mavwp.MAVWPLoader()
seq = 0
for waypoint in enumerate(waypoints):
    seq = waypoint[0]
    lat, lon = waypoint[1]
    #lon = waypoint[1]
    altitude = 100
    autocontinue = 1
    current = 0
    param1 = 15.0 # minimum pitch
    frame = mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT
    if seq == 0: # first waypoint to takeoff
        current = 1
        p = mavutil.mavlink.MAVLink_mission_item_message(master.target_system, master.target_component, seq, frame,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            current, autocontinue, param1, 0, 0, 0, lat, lon, altitude)
    """
    elif seq == len(waypoints) - 1: # last waypoint to land
        p = mavutil.mavlink.MAVLink_mission_item_message(master.target_system, master.target_component, seq, frame,
            mavutil.mavlink.MAV_CMD_NAV_LAND,
            current, autocontinue, 0, 0, 0, 0, lat, lon, altitude)
    else:
        p = mavutil.mavlink.MAVLink_mission_item_message(master.target_system, master.target_component, seq, frame,
            mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
            current, autocontinue, 0, 0, 0, 0, lat, lon, altitude)
    """
    wp.add(p)


# Send Home location
home_location = waypoints[0]
cmd_set_home(home_location, 0)
msg = master.recv_match(type=['COMMAND_ACK'],blocking=True)
print("%s" % msg)
print("Set home location: %f, %f" %(home_location[0], home_location[1]))

#time.sleep(1)

# Send Waypoint to airframe
print("Send Waypoint to airframe")
master.waypoint_clear_all_send()
master.waypoint_count_send(wp.count())

for i in range(wp.count()):
    msg = master.recv_match(type=['MISSION_REQUEST'],blocking=True)
    print("%s" % msg)
    master.mav.send(wp.wp(msg.seq))
    print("Sending waypoint %d" % msg.seq)

msg = master.recv_match(type=['MISSION_ACK'],blocking=True) # OKAY
print ("%s, %d" % (msg, msg.type))

# Read Waypoint from airframe
master.waypoint_request_list_send()
waypoint_count = 0

msg = master.recv_match(type=['MISSION_COUNT'],blocking=True)
waypoint_count = msg.count
print("%d", msg.count)

for i in range(waypoint_count):
    master.waypoint_request_send(i)
    msg = master.recv_match(type=['MISSION_ITEM'],blocking=True)
    print ("Receving waypoint %d" % msg.seq)
    print ("%s" % msg)

master.mav.mission_ack_send(master.target_system, master.target_component, 0) # OKAY

# Change Mission Mode
PX4_CUSTOM_MAIN_MODE_AUTO = 4.0
PX4_CUSTOM_SUB_MODE_AUTO_MISSION = 4.0
PX4_CUSTOM_SUB_MODE_AUTO_RTL = 5.0

auto_mode_flags =  mavutil.mavlink.MAV_MODE_FLAG_AUTO_ENABLED | mavutil.mavlink.MAV_MODE_FLAG_STABILIZE_ENABLED | mavutil.mavlink.MAV_MODE_FLAG_GUIDED_ENABLED

PX4_MAV_MODE = mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | auto_mode_flags

#master.mav.set_mode_send(1, PX4_MAV_MODE, PX4_CUSTOM_MAIN_MODE_AUTO)

#msg = master.recv_match(type=['COMMAND_ACK'], blocking=True)
#print ("%s", msg)

# Send Heartbeat
master.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 192, 0, 4)

master.wait_heartbeat()
print("HEARTBEAT OK\n")

change_flight_mode("hold")
time.sleep(5)

# Arming
master.mav.command_long_send(
    		master.target_system,
		master.target_component,
	    	mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
    		0,
    		1, 0, 0, 0, 0, 0, 0)

msg = master.recv_match(type=['COMMAND_ACK'],blocking=True)
print ("%s" % msg)

#time.sleep(2)

print ("%d" % master.target_system)
print ("%d" % master.target_component)
"""
master.mav.command_long_send(
                master.target_system,  # target_system
                master.target_component, # target_component
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, # command
                0, # confirmation
                0, # param1
                0, # param2
                0, # param3
                0, # param4
                0, # param5
                0, # param6
                50) # param7- altitude
msg = master.recv_match(type=['COMMAND_ACK'],blocking=True)
print ("%s" % msg)
"""
goal_throttle = 1500
t1 = threading.Thread(target=throttle_th, args=())
t1.daemon = True
t1.start()

t2 = threading.Thread(target=read_loop, args=())
t2.daemon = True
t2.start()

mutated_log = open("mutated_log.txt", "w")
mutated_log.close()

guidance_log = open("guidance_log.txt", "w")
guidance_log.close()

# MISSION START
master.mav.command_long_send(1, 1, mavutil.mavlink.MAV_CMD_MISSION_START, 0, 0, 0, 0, 0, 0, 0, 0)
#msg = master.recv_match(type=['COMMAND_ACK'],blocking=True)
#print msg

time.sleep(10)
#change_flight_mode("altitude")
change_flight_mode("hold")

# Set some preconditions to test a policy
# To do: when I switch to another target policy, I need to update the 'Precondition_path'.
Precondition_path += "./policies/"
Precondition_path += Current_policy
Precondition_path += "/preconditions.txt"
set_preconditions(Precondition_path)

# Check liveness of the RV software
t3 = threading.Thread(target=check_liveness, args=())
t3.daemon = True
t3.start()

"""
for i in range(0, 11):
    # Debug
    print("Try to set %s flight mode!" % flight_mode[i])
    change_flight_mode(flight_mode[i])
    time.sleep(5)
"""

# Main loop
while True:

    global drone_status
    global executing_commands
    global home_altitude
    global current_altitude
    global Armed
    global Parachute_on
    global count_main_loop
    global goal_throttle
    global RV_alive

    print("[Debug] drone_status:%d" %drone_status)
    print("[Debug] Vehicle is grounded, Home alt:%f, Current alt:%f" % (home_altitude, current_altitude))

    # if RV is still active state
    if drone_status == 4:
        Armed = 1
        executing_commands = 1
        print("### Next round (%d) for fuzzing commands. ###" % count_main_loop)
        count_main_loop += 1

        # Calculate propositional and global distances
        calculate_distance(guidance="false")

        pick_up_cmd()

        # Calculate distances to evaluate effect of the executed input
        time.sleep(3)
        calculate_distance(guidance="true")
        goal_throttle = 1500

        for i in range(4):
            set_rc_channel_pwm(i + 1, 1500)

        if Parachute_on == 1:
            Armed = 0
            re_launch()
            count_main_loop = 0

    # The vehicle is grounded
    if (current_altitude < home_altitude) or ((drone_status == 3) and (RV_alive == 1)):
        #print("[Debug] drone_status:%d" % drone_status)
        print("### Vehicle is grounded, Home alt:%f, Current alt:%f" % (home_altitude, current_altitude))
        Armed = 0
        re_launch()
        count_main_loop = 0

    # It is in mayday and going down
    if drone_status == 6:
        Armed = 0
        for i in range(1, 5):
            print("@@@@@@@@@@ Drone losts control. It is in mayday and going down @@@@@@@@@@")

print("-------------------- Fuzzing End --------------------")

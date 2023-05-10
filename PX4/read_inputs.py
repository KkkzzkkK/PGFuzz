"""
	Author: Hyungsub Kim
	Date: 05/20/2020
	Name of file: read_inputs.py
	Goal: Parsing a meta file for inputs
"""
#!/usr/bin/python

param_name = []
param_reboot = []
param_default = []
param_min = []
param_max = []
param_units = []

cmd_name = []
cmd_number = []

env_name = []

#-------------------------------------------------------------------------------
#-------------------------------------------------------------------------------
def parsing_parameter(filepath):

	print('##### (Start) Read a meta file for parameters #####')
	#filepath = 'meta_parameters.txt'
	for cnt, line in enumerate(open(filepath, 'r'), start=1):
		row = line.rstrip().split(',')
		param_name.append(row[0])
		param_reboot.append(row[1])
		param_default.append(row[2])
		param_min.append(row[3])
		param_max.append(row[4])
		param_units.append(row[5])
		print(f"# {cnt} {row[0]} {row[1]} {row[2]} {row[3]} {row[4]} {row[5]}")


	print("##### The name of parameters #####");
	print(param_name)
	print(param_reboot)
	print(param_default)
	print(param_min)
	print(param_max)
	print(param_units)

	print('##### (End) Read a meta file for parameters #####')
#-------------------------------------------------------------------------------
#-------------------------------------------------------------------------------

def parsing_command(filepath):

	print('##### (Start) Read a meta file for user commands #####')
	for line in open(filepath, 'r'):
		#row = line.replace("\n", "")
		row = line.rstrip().split(',')
		cmd_name.append(row[0])
		cmd_number.append(row[1])
	print("##### The name of user commands #####");
	print(cmd_name)
	print(cmd_number)
	print('##### (End) Read a meta file for user commands #####')
#-------------------------------------------------------------------------------
#-------------------------------------------------------------------------------

def parsing_env(filepath):

	print('##### (Start) Read a meta file for environmental factors #####')
	for line in open(filepath, 'r'):
		row = line.replace("\n", "")
		env_name.append(row)
	print("##### The name of environmental factors #####");
	print(env_name)
	print('##### (End) Read a meta file for environmental factors #####')

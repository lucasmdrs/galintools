#!/usr/bin/python
import argparse, os, json, syslog, sqlite3, subprocess, boto.ec2
from galintools import infra_common, aws
from galintools.settings import *

def sqllite3_execute(cursor,query,error_msg):
	try:
		result = cursor.execute(query)
		conn.commit()
	except Exception, e:
		logger.exception("%s. Details: %s" % (error_msg, e))
		exit(1)

	return result

# Command line parsing
parser = argparse.ArgumentParser(description='Nodervisor auto-registration')

parser.add_argument('-r','--region',
					default=settings['DEFAULT_REGION'], 
					choices=settings['REGIONS'], 
					help='AWS Region')

parser.add_argument('-a','--action', 
					choices=['add','delete'],
					required=True,
					help='Action to execute')

parser.add_argument('-c','--config', 
					help='Config file')

parser.add_argument('-t','--tag', 
					required=True, 
					help='Tag for search')

parser.add_argument('-d','--nodervisordb', 
					required=True, 
					help='Nodervisor database file')

parser.add_argument('-s','--nodervisorservice', 
					default="nodervisor",
					help='Nodervisor service name')

args = parser.parse_args()

utils = infra_common.Utils()

if args.config:
	config_parsed = utils.load_json_config(args.config)
	if config_parsed == {}:
		exit(1)

try:
	logger = utils.create_new_logger(log_config=config_parsed['log'],
									 log_name=os.path.basename(__file__))
except Exception, e:
	logger = utils.create_new_logger(log_config=settings['log'],
									 log_name=os.path.basename(__file__))

if logger == 1:
	exit(1)

aws_ec2 = aws.Ec2(logger=logger, region=args.region)

try:
	conn = sqlite3.connect(args.nodervisordb)
except Exception, e:
	logger.exception("Can't connect to Nodervisor database: %s. Details: %s" % (args.nodervisordb,e))
	exit(1)

c = conn.cursor()

restart_service = False

if args.action == 'add':
	instances = aws_ec2.get_instance_obj(filters={'tag-key' : args.tag})

	for instance in instances:
		instance_hostname = instance.tags['Name'] + '-' + instance.id
		instance_group = instance.tags['Name']
		supervisor_uri = 'http://%s:%s' % (instance.private_ip_address, instance.tags[args.tag])

		logger.debug("Searching host %s in Nodervisor database" % (instance_hostname))
		result = sqllite3_execute(c,
						  		  "select Name from hosts where Name = '%s'" % (instance_hostname),
						  		  "Can't search for hosts in Nodervisor database: %s" % (args.nodervisordb))
		
		if not result.fetchone():
			logger.debug("Host %s not found in Nodervisor database" % (instance_hostname))

			logger.debug("Searching for group %s in Nodervisor database" % (instance_group))
			result = sqllite3_execute(c,
									  "select Name from groups where Name = '%s'" % (instance_group),
									  "Can't search for groups in Nodervisor database: %s" % (args.nodervisordb))

			if not result.fetchone():
				logger.info("Creating group %s in Nodervisor database" % (instance_group))
				sqllite3_execute(c,
								"insert into groups (Name) values ('%s')" % (instance_group),
								"Can't insert group %s in Nodervisor database: %s" % (instance_group,args.nodervisordb))

			result = sqllite3_execute(c,
									  "select idGroup from groups where Name = '%s'" % (instance_group),
									  "Can't search for groups in Nodervisor database: %s" % (args.nodervisordb))

			groupid = result.fetchone()[0]

			logger.info("Creating host %s in Nodervisor database" % (instance_hostname))
			sqllite3_execute(c,
							"insert into hosts (idGroup,Name,Url) values (%i,'%s','%s')" % (groupid,instance_hostname,supervisor_uri),
							"Can't insert group %s in Nodervisor database: %s" % (instance_hostname,args.nodervisordb))

			restart_service = True

		else:
			logger.debug("Host %s found in Nodervisor database. Ignoring..." % (instance_hostname))

elif args.action == 'delete':
	logger.debug("Searching hosts in Nodervisor database")
	result = sqllite3_execute(c,
					  		  "select Name,idGroup from hosts",
					  		  "Can't search for hosts in Nodervisor database: %s" % (args.nodervisordb))
	
	for host in result.fetchall():
		hostname = host[0]
		groupid = host[1]
		instance = aws_ec2.get_instance_obj(filters={'tag-key' : args.tag, 'tag:Name' : hostname})

		if not instance:
			logger.info("Deleting host %s in Nodervisor database" % (hostname))
			sqllite3_execute(c,
				  		  	 "delete from hosts where Name = '%s';" % (hostname),
						  	 "Can't delete host %s in Nodervisor database: %s" % (hostname,args.nodervisordb))
			
			result = sqllite3_execute(c,
				  				  	  "select Name from hosts where idGroup = %i" % (groupid),
						  			  "Can't search for host %s in Nodervisor database: %s" % (hostname,args.nodervisordb))

			if not result.fetchone():
				logger.info("Deleting groupid %i in Nodervisor database" % (groupid))
				sqllite3_execute(c,
					  		  	 "delete from groups where idGroup = %i;" % (groupid),
							  	 "Can't delete groupid %s in Nodervisor database: %s" % (groupid,args.nodervisordb))

			restart_service = True

if restart_service:
	if not utils.restart_upstart_service(args.nodervisorservice, logger):
		utils.set_return_code(1)

exit(utils.return_code)
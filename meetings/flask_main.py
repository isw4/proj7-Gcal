import flask
from flask import render_template
from flask import request
from flask import url_for
import uuid

import json
import logging

# Date/time and timezone handling 
import arrow # Replacement for datetime, based on moment.js
from dateutil import tz  # For interpreting local times

# OAuth2  - Google library implementation for convenience
from oauth2client import client
import httplib2   # used in oauth2 flow

# Google API for services 
from apiclient import discovery

# Functions to help get and process information from Google Calendars
from from_gcal import list_calendars, list_instances_btwn_times_in_dates


###
# Globals
###
import config
if __name__ == "__main__":
	CONFIG = config.configuration()
else:
	CONFIG = config.configuration(proxied=True)

app = flask.Flask(__name__)
app.debug=CONFIG.DEBUG
app.logger.setLevel(logging.DEBUG)
app.secret_key=CONFIG.SECRET_KEY

SCOPES = 'https://www.googleapis.com/auth/calendar.readonly'
CLIENT_SECRET_FILE = CONFIG.GOOGLE_KEY_FILE  ## You'll need this
APPLICATION_NAME = 'MeetMe class project'

#############################
#
#  Views
#
#############################

@app.route("/")
@app.route("/index")
def index():
	app.logger.debug("Entering index page")
	if 'begin_date' not in flask.session:
		init_session_values()
	return render_template('index.html')


@app.route("/display")
def render_display():
	"""
	Gets all the information needed to display on the page. On the first submission,
	it gets the calenders from Google, authorizing if needed. It then renders the page.
	On second submission, when the user has checked the calendars to use, it gets the
	correct event instances, then renders the page.
	"""
	app.logger.debug("Getting Calendars. Checking Google Calendar credentials")
	credentials = valid_credentials()
	if not credentials:
		return flask.redirect(flask.url_for("authorize"))
	else:
		app.logger.debug("Have Google Calendar credentials")
		gcal_service = get_gcal_service(credentials)
		app.logger.debug("Returned from get_gcal_service. Getting Calendars")
		flask.session['calendars'] = list_calendars(gcal_service)
	
	if not flask.session['selected_cal']:
		app.logger.debug("No calendars already selected")
		flask.session['busytimes'] = []
		pass
		# End of first submit
	else:
		# In the second submit, if user has selected calendars
		app.logger.debug("Getting busy event instances from these selected calendars: {}".format(flask.session['selected_cal']))
		flask.session['busytimes'] = list_instances_btwn_times_in_dates(gcal_service, flask.session['selected_cal'], 
																		flask.session['begin_date'], flask.session['end_date'],
																		flask.session['begin_time'], flask.session['end_time'])
		# End of second submit

	return render_template('index.html')


#####
#
#  Option setting:  Buttons or forms that add some
#     information into session state.  Don't do the
#     computation here; use of the information might
#     depend on what other information we have.
#   Setting an option sends us back to the main display
#      page, where we may put the new information to use. 
#
#####

@app.route("/setdata", methods=['POST'])
def set_data():
	"""
	Gets option information and sets cookies
	"""
	app.logger.debug("In set_data with request: {}".format(request.form))
	
	# Time
	flask.session['begin_time'] = interpret_time(request.form.get('begin_time'))
	flask.session['end_time'] = interpret_time(request.form.get('end_time'))
	
	assert arrow.get(flask.session['begin_time']) <= arrow.get(flask.session['end_time'])
	app.logger.debug("Begin and end times make sense")

	# Date
	daterange = request.form.get('daterange')
	flask.session['daterange'] = daterange
	daterange_parts = daterange.split()
	flask.session['begin_date'] = interpret_date(daterange_parts[0])
	flask.session['end_date'] = interpret_date(daterange_parts[2])

	# Calendar selections
	selections = request.form.getlist("checkbox")
	if not selections:
		app.logger.debug("No calendars selected")
		flask.session['selected_cal'] = []
	else:
		app.logger.debug("Selected calendars are: {}".format(selections))
		flask.session['selected_cal'] = selections

	return flask.redirect(url_for('render_display'))


####
#
#  Google calendar authorization:
#      Returns us to the main /choose screen after inserting
#      the calendar_service object in the session state.  May
#      redirect to OAuth server first, and may take multiple
#      trips through the oauth2 callback function.
#
#  Protocol for use ON EACH REQUEST: 
#     First, check for valid credentials
#     If we don't have valid credentials
#         Get credentials (jump to the oauth2 protocol)
#         (redirects back to /choose, this time with credentials)
#     If we do have valid credentials
#         Get the service object
#
#  The final result of successful authorization is a 'service'
#  object.  We use a 'service' object to actually retrieve data
#  from the Google services. Service objects are NOT serializable ---
#  we can't stash one in a cookie.  Instead, on each request we
#  get a fresh serivce object from our credentials, which are
#  serializable. 
#
#  Note that after authorization we always redirect to /choose;
#  If this is unsatisfactory, we'll need a session variable to use
#  as a 'continuation' or 'return address' to use instead. 
#
####

def valid_credentials():
	"""
	Returns OAuth2 credentials if we have valid
	credentials in the session.  This is a 'truthy' value.
	Return None if we don't have credentials, or if they
	have expired or are otherwise invalid.  This is a 'falsy' value. 
	"""
	if 'credentials' not in flask.session:
		return None

	credentials = client.OAuth2Credentials.from_json(flask.session['credentials'])

	if (credentials.invalid or credentials.access_token_expired):
		return None

	return credentials


def get_gcal_service(credentials):
	"""
	We need a Google calendar 'service' object to obtain
	list of calendars, busy times, etc.  This requires
	authorization. If authorization is already in effect,
	we'll just return with the authorization. Otherwise,
	control flow will be interrupted by authorization, and we'll
	end up redirected back to /choose *without a service object*.
	Then the second call will succeed without additional authorization.
	"""
	app.logger.debug("Entering get_gcal_service")
	http_auth = credentials.authorize(httplib2.Http())
	service = discovery.build('calendar', 'v3', http=http_auth)
	app.logger.debug("Returning service")
	return service


@app.route("/authorize")
def authorize():
	"""
	Checking one last time for valid credentials, even though a check may
	have been done in order to branch into this redirect.
	"""
	app.logger.debug("Authorizing")
	credentials = valid_credentials()
	if not credentials:
		app.logger.debug("Redirecting to authorization")
		return flask.redirect(flask.url_for('oauth2callback'))

	return flask.redirect(url_for("render_display"))


@app.route('/oauth2callback')
def oauth2callback():
	"""
	The 'flow' has this one place to call back to.  We'll enter here
	more than once as steps in the flow are completed, and need to keep
	track of how far we've gotten. The first time we'll do the first
	step, the second time we'll skip the first step and do the second,
	and so on.
	"""
	app.logger.debug("Entering oauth2callback")
	flow =  client.flow_from_clientsecrets(
		CLIENT_SECRET_FILE,
		scope= SCOPES,
		redirect_uri=flask.url_for('oauth2callback', _external=True))
	## Note we are *not* redirecting above.  We are noting *where*
	## we will redirect to, which is this function. 

	## The *second* time we enter here, it's a callback 
	## with 'code' set in the URL parameter.  If we don't
	## see that, it must be the first time through, so we
	## need to do step 1. 
	app.logger.debug("Got flow")
	if 'code' not in flask.request.args:
		app.logger.debug("Code not in flask.request.args")
		auth_uri = flow.step1_get_authorize_url()
		app.logger.debug(auth_uri)
		return flask.redirect(auth_uri)
	## This will redirect back here, but the second time through
	## we'll have the 'code' parameter set
	else:
		## It's the second time through ... we can tell because
		## we got the 'code' argument in the URL.
		app.logger.debug("Code was in flask.request.args")
		auth_code = flask.request.args.get('code')
		credentials = flow.step2_exchange(auth_code)
		flask.session['credentials'] = credentials.to_json()
		## Now I can build the service and execute the query,
		## but for the moment I'll just log it and go back to
		## the main screen
		app.logger.debug("Got credentials")
		return flask.redirect(flask.url_for('authorize'))


####
#
#   Initialize session variables 
#
####

def init_session_values():
	"""
	Start with some reasonable defaults for date and time ranges.
	Note this must be run in app context ... can't call from main. 
	"""
	# Default date span = tomorrow to 1 week from now
	now = arrow.now('local')     # We really should be using tz from browser
	tomorrow = now.replace(days=+1)
	nextweek = now.replace(days=+7)
	flask.session["begin_date"] = tomorrow.floor('day').isoformat()
	flask.session["end_date"] = nextweek.ceil('day').isoformat()
	flask.session["daterange"] = "{} - {}".format(
		tomorrow.format("MM/DD/YYYY"),
		nextweek.format("MM/DD/YYYY"))
	# Default time span each day, 8 to 5
	flask.session["begin_time"] = interpret_time("9am")
	flask.session["end_time"] = interpret_time("5pm")
	flask.session['selected_cal'] = []


def interpret_time( text ):
	"""
	Read time in a human-compatible format and
	interpret as ISO format with local timezone.
	May throw exception if time can't be interpreted. In that
	case it will also flash a message explaining accepted formats.
	"""
	app.logger.debug("Decoding time '{}'".format(text))
	time_formats = ["ha", "h:mma",  "h:mm a", "H:mm"]
	try: 
		as_arrow = arrow.get(text, time_formats).replace(tzinfo=tz.tzlocal())
		as_arrow = as_arrow.replace(year=2016) #HACK see below
		app.logger.debug("Succeeded interpreting time")
	except:
		app.logger.debug("Failed to interpret time")
		flask.flash("Time '{}' didn't match accepted formats 13:30 or 1:30pm"
			  .format(text))
		raise
	return as_arrow.isoformat()
	#HACK #Workaround
	# isoformat() on raspberry Pi does not work for some dates
	# far from now.  It will fail with an overflow from time stamp out
	# of range while checking for daylight savings time.  Workaround is
	# to force the date-time combination into the year 2016, which seems to
	# get the timestamp into a reasonable range. This workaround should be
	# removed when Arrow or Dateutil.tz is fixed.
	# FIXME: Remove the workaround when arrow is fixed (but only after testing
	# on raspberry Pi --- failure is likely due to 32-bit integers on that platform)


def interpret_date( text ):
	"""
	Convert text of date to ISO format used internally,
	with the local time zone.
	"""
	try:
	  as_arrow = arrow.get(text, "MM/DD/YYYY").replace(
		  tzinfo=tz.tzlocal())
	except:
		flask.flash("Date '{}' didn't fit expected format 12/31/2001")
		raise
	return as_arrow.isoformat()


def next_day(isotext):
	"""
	ISO date + 1 day (used in query to Google calendar)
	"""
	as_arrow = arrow.get(isotext)
	return as_arrow.replace(days=+1).isoformat()


#################
#
# Functions used within the templates
#
#################


@app.template_filter( 'fmtdate' )
def format_arrow_date( date ):
	try: 
		normal = arrow.get( date )
		return normal.format("ddd MM/DD/YYYY")
	except:
		return "(bad date)"


@app.template_filter( 'fmttime' )
def format_arrow_time( time ):
	try:
		normal = arrow.get( time )
		return normal.format("HH:mm")
	except:
		return "(bad time)"
	
#############


if __name__ == "__main__":
  # App is created above so that it will
  # exist whether this is 'main' or not
  # (e.g., if we are running under green unicorn)
  app.run(port=CONFIG.PORT,host="0.0.0.0")
	

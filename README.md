Overview
========

PyDynDNS sends dynamic DNS updates to a DNS server. PyDynDNS’s principles are
as follows:
* PyDynDNS allows a computer to register *its own* address on a DNS server.
  PyDynDNS will not help a DHCP server register all its clients. PyDynDNS is
  useful for situations where the DHCP server in use is not able to issue DNS
  updates.
* PyDynDNS uses the DNS protocol to perform dynamic updates. Many commercial
  dynamic DNS providers use HTTP-based update interfaces instead. PyDynDNS does
  not support those interfaces.
* PyDynDNS will update A and AAAA records only.
* PyDynDNS is small and lightweight and is intended to be run from a Cron job
  or DHCP client address-change callback. It is perfectly reasonable to run
  PyDynDNS every minute or so. It will only send updates when changes have been
  made.



Dependencies
============

PyDynDNS requires the following packages, all of which are available via `pip`:
* dnspython
* ifaddr



Usage
=====

PyDynDNS can be invoked from the command line. It uses standard command-line
option parsing and understand the `-h` and `--help` options to display usage
information.

The name to register is taken from the computer’s current hostname. The
addresses to register is taken from one or more network interfaces, which are
passed on the command line. The server to talk to is taken from the SOA record
covering the computer’s hostname. Each update deletes all records associated
with the hostname then registers a new A or AAAA record for each of the host’s
IP addresses.

A pair of Windows Task Scheduler job files are provided in the
`examples/tasksched` subdirectory. These assume PyDynDNS has been installed at
`C:\Python3.7\Bin\PyDynDNS`. The `Normal.xml` job runs every ten minutes,
starting ten minutes after system boot, to keep the DNS names up to date as IP
addresses change. The `Force.xml` job runs once at boot with the `-f` option,
to ensure that updates other operating systems may have made are flushed out
and the system’s current address is registered properly.



Configuration File
==================

PyDynDNS uses a JSON-formatted configuration file. The top-level configuration
file must be a JSON object with the following keys:
* cache (required, string or boolean): The name of the cache file, `true` to
  use the platform-default cache file, or `false` to not use a cache file.
  PyDynDNS writes into this file each time it performs an update. When invoked,
  it first checks the cache file to decide whether an update needs to be
  performed; if no data has changed compared to the cache file, the update is
  skipped. On a single-OS computer, this file can be stored anywhere. On a
  multi-OS computer, this file should probably be stored somewhere that is
  destroyed on reboot, so that any registration changes made while other OSes
  are booted will be overwritten. If omitted, no cache file is used and every
  invocation results in an update being sent.
* ipv4 (required, boolean): Whether or not to register IPv4 (A) records.
* ipv6 (required, object): Configuration regarding IPv6; see below.
* logging (required, object): A logging configuration, as described by the
  Python logging configuration dictionary schema at
  <https://docs.python.org/3/library/logging.config.html#logging-config-dictschema>.
  Note that a logger named `pydyndns` is used for all output.
* ttl (required, number): The time to live for created DNS records, in seconds.
* tsig (optional, object): Configuration regarding TSIG authentication; see below.


ipv6 object
-----------

The ipv6 object contains the following keys:
* enable (required, boolean): Whether or not to register IPv6 (AAAA) records.
* teredo (required, boolean): Whether to include Teredo addresses.


tsig object
-----------

The tsig object, if present, contains the following keys:
* algorithm (required, string): The name of the TSIG algorithm to use, one of
  `hmac-md5`, `hmac-sha1`, `hmac-sha224`, `hmac-sha256`, `hmac-sha384`, or
  `hmac-sha512`.
* key (required, string): The base-64-encoded shared secret.
* keyname (required, string): The name of the key, which must match the key
  name the server is expecting.


Example logger object for UNIX syslog dæmon output
--------------------------------------------------

```JSON
{
	"version": 1,
	"formatters": {
		"syslog": {
			"format": "%(name)s: %(message)s"
		}
	},
	"handlers": {
		"syslog": {
			"class": "logging.handlers.SysLogHandler",
			"address": "/dev/log",
			"facility": "local0",
			"formatter": "syslog"
		}
	},
	"root": {
		"level": "WARNING",
		"handlers": ["syslog"]
	}
}
```


Example logger object for Windows event logger output
-----------------------------------------------------

This requires Python Win32 extensions to be installed (`python -m pip install
pypiwin32`).

If you are running under a low-privilege Windows account (e.g. Local Service),
you will be able to write messages to the event log, but not register new event
sources. Running PyDynDNS once as an adminstrator with the NTEventLogHandler
configured will register the event source, after which subsequent invocations
can be made from the low-privilege account.

```JSON
{
	"version": 1,
	"formatters": {
		"eventlog": {
			"format": "%(message)s"
		}
	},
	"handlers": {
		"eventlog": {
			"class": "logging.handlers.NTEventLogHandler",
			"appname": "PyDynDNS",
			"formatter": "eventlog"
		}
	},
	"root": {
		"level": "WARNING",
		"handlers": ["eventlog"]
	}
}
```

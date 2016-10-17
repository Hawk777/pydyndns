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
* PyDynDNS will update AAAA records only.
* PyDynDNS is small and lightweight and is intended to be run from a Cron job
  or DHCP client address-change callback. It is perfectly reasonable to run
  PyDynDNS every minute or so. It will only send updates when changes have been
  made.



Usage
=====

PyDynDNS can be invoked from the command line. It uses standard command-line
option parsing and understand the `-h` and `--help` options to display usage
information.

The name to register is taken from the computer’s current hostname. The
addresses to register is taken from one or more network interfaces, which are
passed on the command line. The server to talk to is taken from the SOA record
covering the computer’s hostname. Each update deletes all records associated
with the hostname then registers a new AAAA record for each of the host’s IPv6
addresses.



Configuration File
==================

PyDynDNS uses a JSON-formatted configuration file. The top-level configuration
file must be a JSON object with the following keys:
* cache (optional, string): The name of the cache file. PyDynDNS writes into
  this file each time it performs an update. When invoked, it first checks the
  cache file to decide whether an update needs to be performed; if no data has
  changed compared to the cache file, the update is skipped. On a single-OS
  computer, this file can be stored anywhere. On a multi-OS computer, this file
  should probably be stored somewhere that is destroyed on reboot, so that any
  registration changes made while other OSes are booted will be overwritten. If
  omitted, no cache file is used and every invocation results in an update
  being sent.
* logging (required, object): A logging configuration, as described by the
  Python logging configuration dictionary schema at
  <https://docs.python.org/3/library/logging.config.html#logging-config-dictschema>.
  Note that a logger named `pydyndns` is used for all output.
* ttl (required, number): The time to live for created DNS records, in seconds.

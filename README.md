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

PyDynDNS can be invoked from the command line. It accepts a single parameter
which is the name of the network interface whose information should be
registered. For example, you might run `pydyndns enp3s0` to register your
Ethernet interface’s address.

The computer’s current hostname is used as the name to register. The SOA record
associated with that hostname is used to find the primary authoritative
nameserver to which updates are sent. Each update deletes all records
associated with the hostname then registers a new AAAA record for the host’s
IPv6 address.

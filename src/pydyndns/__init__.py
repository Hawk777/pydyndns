#!/usr/bin/env python3

import abc
import argparse
import json
import logging
import logging.config
import logging.handlers
import os
import socket
import sys
import typing

import dns.name
import dns.rdataclass
import dns.rdatatype
import dns.rdtypes.IN.A
import dns.rdtypes.IN.AAAA
import dns.resolver
import dns.tsigkeyring
import dns.update
import netifaces


class Platform(metaclass=abc.ABCMeta):
    """
    Encapsulates knowledge about a specific operating system.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """The name as shown in os.name."""
        pass

    @abc.abstractmethod
    def permanent_ipv6_addresses(self, addresses: typing.List[str]) -> typing.List[str]:
        """
        Return only the IPv6 addresses that are permanent (i.e. not generated
        by RFC4941 privacy extensions).

        For IPv6, most NICs will have multiple addresses: one permanent and a
        bunch of temporary generated by SLAAC privacy extensions. We should
        include only the permanent address, not the temporary ones.

        This is platform-specific because netifaces does not return any
        metadata about the address indicating whether it is permanent or
        temporary (and the addresses themselves are indistinguishable).
        However, specific operating systems appear to have a consistent
        ordering as to whether permanent addresses are returned before or after
        temporary addresses.
        """
        pass

    @property
    @abc.abstractmethod
    def default_config_filename(self) -> str:
        """Return the default location of the configuration file."""
        pass

    @property
    @abc.abstractmethod
    def default_cache_filename(self) -> str:
        """Return the default location of the cache file."""
        pass

    @abc.abstractmethod
    def platform_specific_setup(self) -> None:
        """
        Do any work that is specific to this platform for initializing the
        program.
        """
        pass


class POSIXPlatform(Platform):
    @property
    def name(self) -> str:
        return "posix"

    def permanent_ipv6_addresses(self, addresses: typing.List[str]) -> typing.List[str]:
        # Linux returns permanent addresses last.
        if len(addresses) == 0:
            return []
        else:
            return [addresses[-1]]

    @property
    def default_config_filename(self) -> str:
        return "/etc/pydyndns.conf"

    @property
    def default_cache_filename(self) -> str:
        return "/run/pydyndns.cache"

    def platform_specific_setup(self) -> None:
        pass


class WindowsPlatform(Platform):
    @property
    def name(self) -> str:
        return "nt"

    def permanent_ipv6_addresses(self, addresses: typing.List[str]) -> typing.List[str]:
        # Windows returns permanent addresses first.
        if len(addresses) == 0:
            return []
        else:
            return [addresses[0]]

    @property
    def default_config_filename(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydyndns.conf")

    @property
    def default_cache_filename(self) -> str:
        localAppData = os.environ.get("LOCALAPPDATA")
        if localAppData is None:
            localAppData = os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(localAppData, "Temp", "pydyndns.cache")

    def platform_specific_setup(self) -> None:
        # Python’s NTEventLogHandler class unconditionally tries to add the
        # event source to the Windows registry. This fails when running as a
        # low-privileged account. If somebody had already added the event
        # source then logging events using that source could work, but
        # NTEventLogHandler doesn’t bother trying that, it just fails in
        # construction if the registration fails.  Work around this by
        # swallowing exceptions during source registration.
        try:
            import pywintypes
            import win32evtlogutil
            oldAddSourceToRegistry = win32evtlogutil.AddSourceToRegistry
            def replacement(appname: str, dllname: str, logtype: str) -> None:
                try:
                    oldAddSourceToRegistry(appname, dllname, logtype)
                except pywintypes.error:
                    pass
            win32evtlogutil.AddSourceToRegistry = replacement
        except ImportError as exp:
            # Guess Win32 extensions are not installed.
            pass


class UnknownPlatform(Platform):
    @property
    def name(self) -> str:
        return "unknown"

    def permanent_ipv6_addresses(self, addresses: typing.List[str]) -> typing.List[str]:
        # No idea what the convention is on this platform, so just return all
        # of them.
        return addresses

    @property
    def default_config_filename(self) -> str:
        return "pydyndns.conf"

    @property
    def default_cache_filename(self) -> str:
        return "pydyndns.cache"

    def platform_specific_setup(self) -> None:
        pass


class Family(metaclass=abc.ABCMeta):
    """
    Encapsulates knowledge about a specific address family.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """The name used as a cache key for addresses in this family."""
        pass

    @property
    @abc.abstractmethod
    def netifaces_constant(self) -> int:
        """Return the numeric ID used as a key in netifaces’ output."""
        pass

    @abc.abstractmethod
    def add_address_to_update(self, update: dns.update.Update, hostPart: dns.name.Name, ttl: int, address: str) -> None:
        """Add an address in this family to a DNS update request."""
        pass

    @abc.abstractmethod
    def filter_address_list(self, addresses: typing.Iterable[str]) -> typing.List[str]:
        """
        Return only those addresses that are useful, e.g. not loopback,
        link-local, temporary, or other special addresses that should not be
        registered.
        """
        pass


class IPv4(Family):
    @property
    def name(self) -> str:
        return "ipv4"

    @property
    def netifaces_constant(self) -> int:
        return int(socket.AF_INET)

    def add_address_to_update(self, update: dns.update.Update, hostPart: dns.name.Name, ttl: int, address: str) -> None:
        update.add(hostPart, ttl, dns.rdtypes.IN.A.A(dns.rdataclass.IN, dns.rdatatype.A, address))

    def filter_address_list(self, addresses: typing.Iterable[str]) -> typing.List[str]:
        # For IPv4 most NICs have only one address. It’s not clear that there
        # are any specific rules about how multiple addresses ought to be
        # handled. Just include all of them that are acceptable.
        return [x for x in addresses if self.include_address(x)]

    def include_address(self, address: str) -> bool:
        parts = [int(part) for part in address.split(".")]
        if parts[0] == 127:
            return False # Loopback address
        elif parts[0] >= 240:
            return False # Multicast or reserved address
        return True


class IPv6(Family):
    def __init__(self, platform: Platform, config: typing.Mapping[typing.Any, typing.Any]):
        self._platform = platform
        self._config = config

    @property
    def name(self) -> str:
        return "ipv6"

    @property
    def netifaces_constant(self) -> int:
        return int(socket.AF_INET6)

    def add_address_to_update(self, update: dns.update.Update, hostPart: dns.name.Name, ttl: int, address: str) -> None:
        update.add(hostPart, ttl, dns.rdtypes.IN.AAAA.AAAA(dns.rdataclass.IN, dns.rdatatype.AAAA, address))

    def filter_address_list(self, addresses: typing.Iterable[str]) -> typing.List[str]:
        return self._platform.permanent_ipv6_addresses([x for x in addresses if self.include_address(x)])

    def include_address(self, address: str) -> bool:
        first_word = int(address.split(":")[0] or "0", 16)
        second_word = int(address.split(":")[1] or "0", 16)
        if first_word == 0x0000:
            return False # Unspecified, local, or IPv6-mapped address
        elif first_word == 0x0100:
            return False # Discard address
        elif (first_word & 0xFE00) == 0xFC00:
            return False # Unique local address
        elif first_word == 0xFE80:
            return False # Link-local address
        elif (first_word & 0xFF00) == 0xFF00:
            return False # Multicast address
        elif ((first_word == 0x2001) and (second_word == 0x0000)) and not self._config["teredo"]:
            return False # Teredo address
        return True


def run(platform: Platform, args: argparse.Namespace, config: typing.Mapping[typing.Any, typing.Any], logger: logging.Logger) -> None:
    """
    Run the program.

    platform -- an instance of a subclass of Platform
    args -- a module containing parsed command-line arguments
    config -- a dict containing the parsed configuration file
    logger -- a logger to log messages to
    """
    # Decide which families to use.
    families: typing.List[Family] = []
    if config["ipv4"]:
        families.append(IPv4())
    if config["ipv6"]["enable"]:
        families.append(IPv6(platform, config["ipv6"]))
    if not families:
        logger.error("No address families are enabled.")
        return

    # Grab the TTL from the config file.
    ttl = int(config["ttl"])

    # Decide which cache file to use, if any.
    cache_file: typing.Optional[str]
    if isinstance(config["cache"], str):
        cache_file = config["cache"]
    elif config["cache"] == True:
        cache_file = platform.default_cache_filename
    else:
        cache_file = None
    if cache_file is None:
        logger.debug("Using no cache file.")
    else:
        logger.debug("Using cache file %s.", cache_file)

    # Wipe the cache file if in force mode. Doing this, rather than just
    # unconditionally updating right now, means that if this update fails, the
    # cache file will not be written and the next update will also be
    # unconditional, which is a more useful behaviour for force (you can run in
    # force mode once and be sure that at least one update will happen
    # successfully before we stop trying).
    if args.force and cache_file is not None:
        logger.debug("Wiping cache due to --force.")
        try:
            os.remove(cache_file)
        except OSError:
            pass

    # Load the cache file, if any.
    if cache_file is not None:
        try:
            with open(cache_file, "r") as fp:
                cache = json.load(fp)
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            cache = None
    else:
        cache = None

    # Rip apart my hostname.
    fqdn = dns.name.from_text(socket.getfqdn())
    zone = fqdn.parent()
    hostPart = fqdn.relativize(zone)

    # Find which nameserver we should talk to using an SOA query.
    resp = dns.resolver.query(zone, dns.rdatatype.SOA)
    if len(resp.rrset) != 1:
        raise Exception(f"Got {len(resp.rrset)} SOA records for zone {zone}, expected 1.")
    server = resp.rrset[0].mname.to_text(omit_final_dot=True)
    logger.debug("Using nameserver %s.", server)

    # Find my addresses.
    addresses: typing.Dict[str, typing.List[str]] = {family.name: [] for family in families}
    for interface in (args.interface or netifaces.interfaces()):
        for family in families:
            ifAddresses = netifaces.ifaddresses(interface).get(family.netifaces_constant, [])
            addresses[family.name] += family.filter_address_list([addr["addr"] for addr in ifAddresses])
    for family in families:
        addresses[family.name].sort()

    # Get the hostname and addresses most recently sent from the cache.
    if cache:
        last_hostname = cache.get("hostname")
        last_addresses = cache.get("addresses")
    else:
        last_hostname = None
        last_addresses = None

    # Check if the current hostname and addresses are the same as the last one.
    if fqdn.to_text() == last_hostname and addresses == last_addresses:
        logger.info("Eliding DNS record update for %s to %s as cache says addresses have not changed.", fqdn, addresses)
    else:
        # Construct the DNS update.
        logger.info("Updating DNS record for %s to %s.", fqdn, addresses)
        update = dns.update.Update(zone)
        update.delete(hostPart)
        for family in families:
            for address in addresses[family.name]:
                family.add_address_to_update(update, hostPart, ttl, address)
        if "tsig" in config:
            knownAlgorithms = {
                "hmac-md5": dns.tsig.HMAC_MD5,
                "hmac-sha1": dns.tsig.HMAC_SHA1,
                "hmac-sha224": dns.tsig.HMAC_SHA224,
                "hmac-sha256": dns.tsig.HMAC_SHA256,
                "hmac-sha384": dns.tsig.HMAC_SHA384,
                "hmac-sha512": dns.tsig.HMAC_SHA512,
            }
            if config["tsig"]["algorithm"] not in knownAlgorithms:
                raise ValueError(f"TSIG algorithm {config['tsig']['algorithm']} not recognized.")
            tsigAlgorithm = knownAlgorithms[config["tsig"]["algorithm"]]
            tsigRing = dns.tsigkeyring.from_text({config["tsig"]["keyname"]: config["tsig"]["key"]})
            update.use_tsig(keyring=tsigRing, algorithm=tsigAlgorithm)
            logger.debug("Update will be authenticated with TSIG %s.", tsigAlgorithm)
        else:
            logger.debug("Update will be unauthenticated.")

        # Resolve the nameserver hostname from the SOA record to one or more IP
        # addresses, and try to send an update to each one in turn until one
        # succeeds or they all fail.
        server_addresses = socket.getaddrinfo(server, "domain", type=socket.SOCK_STREAM)
        errors = []
        for (_, _, _, _, sockaddr) in server_addresses:
            try:
                # Send the update.
                server_address = sockaddr[0]
                logger.debug("Sending update to DNS server at %s.", server_address)
                resp = dns.query.tcp(update, where=server_address, timeout=30)
                if resp.rcode() != dns.rcode.NOERROR:
                    raise Exception("Update failed with rcode {resp.rrcode()}.")

                # This update was successful, so no need to try the rest of the
                # nameserver’s addresses.
                break
            except (OSError, dns.exception.DNSException) as exp:
                # Hold onto the error, but don’t report it yet—try the next
                # address instead.
                errors.append(exp)
        else:
            # All the nameserver’s addresses failed. Bail out.
            raise Exception("Unable to contact any nameservers: " + "; ".join(f"{address[4][0]}: {error}" for (address, error) in zip(server_addresses, errors)))

        # Update the cache to remember that we did the update.
        if cache_file is not None:
            with open(cache_file, "w") as fp:
                json.dump({"hostname": fqdn.to_text(), "addresses": addresses}, fp, ensure_ascii=False, allow_nan=False)


def main() -> None:
    # Choose a platform.
    platform: Platform = UnknownPlatform()
    for i in (POSIXPlatform(), WindowsPlatform()):
        if i.name == os.name:
            platform = i
    platform.platform_specific_setup()

    # Parse command-line arguments.
    parser = argparse.ArgumentParser(description="Dynamically update DNS records.")
    parser.add_argument("-c", "--config", default=platform.default_config_filename, type=str, help=f"which configuration file to read (default: {platform.default_config_filename})", metavar="FILE")
    parser.add_argument("-f", "--force", action="store_true", help="update even if cache says unnecessary")
    parser.add_argument("interface", nargs="*", help="the name of an interface whose address(es) to register (default: all interfaces)")
    args = parser.parse_args()

    # Load configuration file.
    with open(args.config, "r") as configFile:
        config = json.load(configFile)

    # Configure logging.
    if "logging" in config:
        logging.config.dictConfig(config["logging"])
    else:
        logging.basicConfig(level=logging.DEBUG)
        logging.warn("No logging section in config file.")
    logging.captureWarnings(True)

    # Run the program.
    try:
        run(platform, args, config, logging.getLogger("pydyndns"))
    except (KeyboardInterrupt, SystemExit):
        raise
    except:
        logging.getLogger("pydyndns").error("Unhandled exception", exc_info=True)

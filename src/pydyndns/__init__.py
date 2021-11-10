#!/usr/bin/env python3

import abc
import argparse
import ipaddress
import json
import logging
import logging.config
import logging.handlers
import os
import pathlib
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


IPAddressUnion = typing.Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


class IPAddressEnabledJSONEncoder(json.JSONEncoder):
    """
    A JSON encoder that is capable of encoding IP address objects into strings.
    """
    def default(self, obj: typing.Any) -> typing.Any:
        if isinstance(obj, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            return str(obj)
        else:
            return super().default(obj)


class Platform(metaclass=abc.ABCMeta):
    """
    Encapsulates knowledge about a specific operating system.
    """

    __slots__ = ()

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """The name as shown in os.name."""
        pass

    @abc.abstractmethod
    def permanent_ipv6_addresses(self, addresses: typing.List[ipaddress.IPv6Address]) -> typing.List[ipaddress.IPv6Address]:
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
    def default_config_filename(self) -> pathlib.Path:
        """Return the default location of the configuration file."""
        pass

    @property
    @abc.abstractmethod
    def default_cache_filename(self) -> pathlib.Path:
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
    __slots__ = ()

    @property
    def name(self) -> str:
        return "posix"

    def permanent_ipv6_addresses(self, addresses: typing.List[ipaddress.IPv6Address]) -> typing.List[ipaddress.IPv6Address]:
        # Linux returns permanent addresses last.
        if len(addresses) == 0:
            return []
        else:
            return [addresses[-1]]

    @property
    def default_config_filename(self) -> pathlib.Path:
        return pathlib.Path("/etc/pydyndns.conf")

    @property
    def default_cache_filename(self) -> pathlib.Path:
        return pathlib.Path("/run/pydyndns.cache")

    def platform_specific_setup(self) -> None:
        pass


class WindowsPlatform(Platform):
    __slots__ = ()

    @property
    def name(self) -> str:
        return "nt"

    def permanent_ipv6_addresses(self, addresses: typing.List[ipaddress.IPv6Address]) -> typing.List[ipaddress.IPv6Address]:
        # Windows returns permanent addresses first.
        if len(addresses) == 0:
            return []
        else:
            return [addresses[0]]

    @property
    def default_config_filename(self) -> pathlib.Path:
        return pathlib.Path(__file__).parent / "pydyndns.conf"

    @property
    def default_cache_filename(self) -> pathlib.Path:
        env_var = os.environ.get("LOCALAPPDATA")
        if env_var is None:
            local_app_data = pathlib.Path.home() / "AppData" / "Local"
        else:
            local_app_data = pathlib.Path(env_var)
        return local_app_data / "Temp" / "pydyndns.cache"

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
            old_add_source_to_registry = win32evtlogutil.AddSourceToRegistry
            def replacement(appname: str, dllname: str, logtype: str) -> None:
                try:
                    old_add_source_to_registry(appname, dllname, logtype)
                except pywintypes.error:
                    pass
            win32evtlogutil.AddSourceToRegistry = replacement
        except ImportError as exp:
            # Guess Win32 extensions are not installed.
            pass


class UnknownPlatform(Platform):
    __slots__ = ()

    @property
    def name(self) -> str:
        return "unknown"

    def permanent_ipv6_addresses(self, addresses: typing.List[ipaddress.IPv6Address]) -> typing.List[ipaddress.IPv6Address]:
        # No idea what the convention is on this platform, so just return all
        # of them.
        return addresses

    @property
    def default_config_filename(self) -> pathlib.Path:
        return pathlib.Path("pydyndns.conf")

    @property
    def default_cache_filename(self) -> pathlib.Path:
        return pathlib.Path("pydyndns.cache")

    def platform_specific_setup(self) -> None:
        pass


class Family(metaclass=abc.ABCMeta):
    """
    Encapsulates knowledge about a specific address family.
    """

    __slots__ = ()

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
    def add_address_to_update(self, update: dns.update.Update, hostPart: dns.name.Name, ttl: int, address: IPAddressUnion) -> None:
        """Add an address in this family to a DNS update request."""
        pass

    @abc.abstractmethod
    def filter_address_list(self, addresses: typing.Iterable[IPAddressUnion]) -> typing.Sequence[IPAddressUnion]:
        """
        Return only those addresses that are useful, e.g. not loopback,
        link-local, temporary, or other special addresses that should not be
        registered.
        """
        pass


class IPv4(Family):
    __slots__ = ()

    @property
    def name(self) -> str:
        return "ipv4"

    @property
    def netifaces_constant(self) -> int:
        return int(socket.AF_INET)

    def add_address_to_update(self, update: dns.update.Update, hostPart: dns.name.Name, ttl: int, address: IPAddressUnion) -> None:
        assert isinstance(address, ipaddress.IPv4Address)
        update.add(hostPart, ttl, dns.rdtypes.IN.A.A(dns.rdataclass.IN, dns.rdatatype.A, str(address)))

    def filter_address_list(self, addresses: typing.Iterable[IPAddressUnion]) -> typing.Sequence[ipaddress.IPv4Address]:
        # For IPv4 most NICs have only one address. It’s not clear that there
        # are any specific rules about how multiple addresses ought to be
        # handled. Just include all of them that are acceptable.
        return [x for x in addresses if isinstance(x, ipaddress.IPv4Address) and self.include_address(x)]

    def include_address(self, address: ipaddress.IPv4Address) -> bool:
        return (address.is_private or address.is_global) and not address.is_link_local and not address.is_loopback


class IPv6(Family):
    __slots__ = (
        "_platform",
        "_config",
    )

    def __init__(self, platform: Platform, config: typing.Mapping[typing.Any, typing.Any]):
        self._platform = platform
        self._config = config

    @property
    def name(self) -> str:
        return "ipv6"

    @property
    def netifaces_constant(self) -> int:
        return int(socket.AF_INET6)

    def add_address_to_update(self, update: dns.update.Update, hostPart: dns.name.Name, ttl: int, address: IPAddressUnion) -> None:
        assert isinstance(address, ipaddress.IPv6Address)
        update.add(hostPart, ttl, dns.rdtypes.IN.AAAA.AAAA(dns.rdataclass.IN, dns.rdatatype.AAAA, str(address)))

    def filter_address_list(self, addresses: typing.Iterable[IPAddressUnion]) -> typing.Sequence[ipaddress.IPv6Address]:
        return self._platform.permanent_ipv6_addresses([x for x in addresses if isinstance(x, ipaddress.IPv6Address) and self.include_address(x)])

    def include_address(self, address: ipaddress.IPv6Address) -> bool:
        if address.teredo is not None and not self._config["teredo"]:
            return False
        return address.ipv4_mapped is None and (address.is_private or address.is_global) and not address.is_link_local and not address.is_loopback


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
    cache_file: typing.Optional[pathlib.Path]
    if isinstance(config["cache"], str):
        cache_file = pathlib.Path(config["cache"])
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
            cache_file.unlink(True)
        except OSError:
            pass

    # Load the cache file, if any.
    if cache_file is not None:
        try:
            with cache_file.open("r") as fp:
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
    addresses: typing.Dict[str, typing.List[IPAddressUnion]] = {family.name: [] for family in families}
    for interface in (args.interface or netifaces.interfaces()):
        for family in families:
            ifAddresses = netifaces.ifaddresses(interface).get(family.netifaces_constant, [])
            addresses[family.name] += family.filter_address_list([ipaddress.ip_address(addr["addr"]) for addr in ifAddresses])
    for family in families:
        addresses[family.name].sort()

    # Get the hostname and addresses most recently sent from the cache.
    if cache:
        last_hostname = cache.get("hostname")
        last_addresses_strings = cache.get("addresses")
        last_addresses: typing.Optional[typing.Dict[str, typing.List[IPAddressUnion]]]
        if last_addresses_strings is not None:
            last_addresses = {family: [ipaddress.ip_address(a) for a in addresses] for (family, addresses) in last_addresses_strings.items()}
        else:
            last_addresses = None
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
            with cache_file.open("w") as fp:
                json.dump({"hostname": fqdn.to_text(), "addresses": addresses}, fp, ensure_ascii=False, allow_nan=False, cls=IPAddressEnabledJSONEncoder)


def main() -> None:
    # Choose a platform.
    platform: Platform = UnknownPlatform()
    for i in (POSIXPlatform(), WindowsPlatform()):
        if i.name == os.name:
            platform = i
    platform.platform_specific_setup()

    # Parse command-line arguments.
    parser = argparse.ArgumentParser(description="Dynamically update DNS records.")
    parser.add_argument("-c", "--config", default=platform.default_config_filename, type=pathlib.Path, help=f"which configuration file to read (default: {platform.default_config_filename})", metavar="FILE")
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

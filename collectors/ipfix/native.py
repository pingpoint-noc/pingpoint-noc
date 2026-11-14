"""Native flow decoder: NetFlow v5, NetFlow v9 and IPFIX (v10) datagrams -> flow dicts, with no nfcapd or nfdump in between.

Pure functions and one small class, no I/O: ``FlowDecoder.decode(exporter_ip, datagram, now_ts)`` returns the flows in one UDP
datagram, in the same shape ``collectors.ipfix.ingestor.parse_flow`` produces (before the internal host is attributed).

A datagram comes from the network, so it is treated as hostile: every read is bounds checked, a datagram that does not parse
is dropped whole (and counted) instead of raising, and what an exporter can make the decoder remember is capped (templates per
exporter, exporters), so a spoofed sender cannot use memory without limit.

Fields understood (IANA IPFIX numbers, which NetFlow v9 shares): 1/85 octets, 2/86 packets, 4 protocol, 7/11 ports, 8/12 IPv4
addresses, 27/28 IPv6 addresses, 10/14 interfaces, 32 ICMP type/code, 225/226 post-NAT IPv4 addresses, 21/22 switched times (v9),
150-153 start and end times (IPFIX). Anything else in a template is skipped by its length.
"""

import ipaddress
import logging
import struct

LOGGER = logging.getLogger("mini_noc.ipfix_native")

MAX_TEMPLATES_PER_EXPORTER = 256
MAX_EXPORTERS = 64
MAX_FIELDS_PER_TEMPLATE = 64
MAX_RECORD_BYTES = 1500  # a flow record longer than a datagram payload cannot be real

PROTOCOL_NAMES = {1: "icmp", 6: "tcp", 17: "udp"}

# IANA numbers
IE_OCTETS, IE_PACKETS, IE_PROTO = 1, 2, 4
IE_SRC_PORT, IE_SRC_V4, IE_INGRESS, IE_DST_PORT, IE_DST_V4, IE_EGRESS = 7, 8, 10, 11, 12, 14
IE_SRC_V6, IE_DST_V6, IE_ICMP = 27, 28, 32
IE_LAST_SWITCHED, IE_FIRST_SWITCHED = 21, 22
IE_OCTETS_TOTAL, IE_PACKETS_TOTAL = 85, 86
IE_START_S, IE_END_S, IE_START_MS, IE_END_MS = 150, 151, 152, 153
IE_NAT_SRC_V4, IE_NAT_DST_V4 = 225, 226


class DecodeError(Exception):
    """The datagram is malformed (truncated, lengths that do not add up)."""


def _uint(data):
    return int.from_bytes(data, "big") if data else 0


def _ip4(data):
    return str(ipaddress.IPv4Address(bytes(data))) if len(data) == 4 else ""


def _ip6(data):
    return str(ipaddress.IPv6Address(bytes(data))) if len(data) == 16 else ""


def _protocol(number):
    return PROTOCOL_NAMES.get(number, str(number))


class FlowDecoder:
    """Keeps the templates each exporter announced and decodes its data records."""

    def __init__(self, max_templates=MAX_TEMPLATES_PER_EXPORTER, max_exporters=MAX_EXPORTERS):
        self.max_templates = max_templates
        self.max_exporters = max_exporters
        self._templates = {}  # (exporter_ip, domain) -> {template_id: [(field_id, length), ...]}
        self.errors = 0
        self.unknown_template = 0

    # -- entry point ---------------------------------------------------------------------------------------------
    def decode(self, exporter_ip, datagram, now_ts):
        """Flows of one datagram (a list, possibly empty). Never raises."""
        try:
            if len(datagram) < 4:
                raise DecodeError("too short")
            version = struct.unpack_from("!H", datagram, 0)[0]
            if version == 5:
                flows = self._decode_v5(datagram, now_ts)
            elif version == 9:
                flows = self._decode_v9(exporter_ip, datagram, now_ts)
            elif version == 10:
                flows = self._decode_ipfix(exporter_ip, datagram, now_ts)
            else:
                raise DecodeError("unknown version %d" % version)
        except (DecodeError, struct.error, ValueError, IndexError):
            self.errors += 1
            return []
        for flow in flows:
            flow["exporter_ip"] = exporter_ip
        return flows

    # -- templates -----------------------------------------------------------------------------------------------
    def _template_table(self, exporter_ip, domain):
        key = (exporter_ip, domain)
        table = self._templates.get(key)
        if table is None:
            if len(self._templates) >= self.max_exporters:
                raise DecodeError("too many exporters")
            table = {}
            self._templates[key] = table
        return table

    def _store_template(self, table, template_id, fields):
        if template_id not in table and len(table) >= self.max_templates:
            raise DecodeError("too many templates")
        table[template_id] = fields

    # -- NetFlow v5 ----------------------------------------------------------------------------------------------
    def _decode_v5(self, data, now_ts):
        if len(data) < 24:
            raise DecodeError("v5 header")
        count, uptime_ms, unix_secs = struct.unpack_from("!HII", data, 2)
        if count > 30 or len(data) < 24 + 48 * count:
            raise DecodeError("v5 count")
        flows = []
        for index in range(count):
            offset = 24 + 48 * index
            src, dst, _nexthop, in_if, out_if, packets, octets, first, last, sport, dport = struct.unpack_from("!IIIHHIIIIHH", data, offset)
            proto = data[offset + 38]
            flows.append({
                "ts_start_utc": self._switched(unix_secs, uptime_ms, first),
                "ts_end_utc": self._switched(unix_secs, uptime_ms, last),
                "proto": _protocol(proto),
                "src_ip": str(ipaddress.IPv4Address(src)),
                "src_port": sport,
                "dst_ip": str(ipaddress.IPv4Address(dst)),
                "dst_port": dport,
                "packets": packets,
                "bytes": octets,
                "in_if": in_if,
                "out_if": out_if,
                "xlat_src_ip": "0.0.0.0",
                "xlat_dst_ip": "0.0.0.0",
            })
        return flows

    @staticmethod
    def _switched(unix_secs, uptime_ms, switched_ms):
        """Time of a v5/v9 flow event: the router's clock is uptime ms, so it is placed relative to the export time."""
        delta_ms = (uptime_ms - switched_ms) & 0xFFFFFFFF  # the counter wraps every 49.7 days
        if delta_ms > 0x7FFFFFFF:  # a "future" value: treat it as now
            delta_ms = 0
        return float(unix_secs) - delta_ms / 1000.0

    # -- NetFlow v9 ----------------------------------------------------------------------------------------------------
    def _decode_v9(self, exporter_ip, data, now_ts):
        if len(data) < 20:
            raise DecodeError("v9 header")
        _count, uptime_ms, unix_secs, _seq, source_id = struct.unpack_from("!HIIII", data, 2)
        table = self._template_table(exporter_ip, source_id)
        flows = []
        offset = 20
        while offset + 4 <= len(data):
            set_id, length = struct.unpack_from("!HH", data, offset)
            if length < 4 or offset + length > len(data):
                raise DecodeError("v9 flowset length")
            body = memoryview(data)[offset + 4:offset + length]
            if set_id == 0:
                self._read_templates(table, body, enterprise_bits=False)
            elif set_id >= 256:
                flows.extend(self._read_data(table, set_id, body, unix_secs, uptime_ms, ipfix=False))
            # set 1 (options templates) and 2-255 carry nothing we use
            offset += length
        return flows


    # -- IPFIX --------------------------------------------------------------------------------------------------------
    def _decode_ipfix(self, exporter_ip, data, now_ts):
        if len(data) < 16:
            raise DecodeError("ipfix header")
        _version, length, export_time, _seq, domain = struct.unpack_from("!HHIII", data, 0)
        if length < 16 or length > len(data):
            raise DecodeError("ipfix length")
        table = self._template_table(exporter_ip, domain)
        flows = []
        offset = 16
        while offset + 4 <= length:
            set_id, set_length = struct.unpack_from("!HH", data, offset)
            if set_length < 4 or offset + set_length > length:
                raise DecodeError("ipfix set length")
            body = memoryview(data)[offset + 4:offset + set_length]
            if set_id == 2:
                self._read_templates(table, body, enterprise_bits=True)
            elif set_id >= 256:
                flows.extend(self._read_data(table, set_id, body, export_time, 0, ipfix=True))
            offset += set_length
        return flows


    def _read_templates(self, table, body, enterprise_bits):
        offset = 0
        while offset + 4 <= len(body):
            template_id, field_count = struct.unpack_from("!HH", body, offset)
            offset += 4
            if field_count == 0 or field_count > MAX_FIELDS_PER_TEMPLATE:
                raise DecodeError("template field count")
            fields = []
            for _ in range(field_count):
                if offset + 4 > len(body):
                    raise DecodeError("template truncated")
                field_id, field_length = struct.unpack_from("!HH", body, offset)
                offset += 4
                if enterprise_bits and field_id & 0x8000:
                    if offset + 4 > len(body):
                        raise DecodeError("enterprise truncated")
                    offset += 4  # enterprise number: a private field, skipped by its length
                    field_id = 0
                fields.append((field_id & 0x7FFF if field_id else 0, field_length))
            if template_id < 256:
                raise DecodeError("template id")
            self._store_template(table, template_id, fields)


    def _read_data(self, table, set_id, body, export_time, uptime_ms, ipfix):
        fields = table.get(set_id)
        if fields is None:
            self.unknown_template += 1  # the template has not arrived yet: the flows of this set are lost, as in every collector
            return []
        flows = []
        offset = 0
        minimum = sum(length for _, length in fields if length != 0xFFFF)
        while offset + max(minimum, 1) <= len(body):
            values = {}
            start = offset
            for field_id, length in fields:
                if length == 0xFFFF:  # IPFIX variable length: one byte, or 255 and two more
                    if not ipfix or offset >= len(body):
                        raise DecodeError("variable length")
                    length = body[offset]
                    offset += 1
                    if length == 255:
                        if offset + 2 > len(body):
                            raise DecodeError("variable length")
                        length = struct.unpack_from("!H", body, offset)[0]
                        offset += 2
                if offset + length > len(body):
                    raise DecodeError("record truncated")
                if field_id:
                    values[field_id] = bytes(body[offset:offset + length])
                offset += length
            if offset == start:
                raise DecodeError("empty record")  # a template of zero-length fields would loop forever
            if offset - start > MAX_RECORD_BYTES:
                raise DecodeError("record too long")
            flow = self._flow_from_values(values, export_time, uptime_ms, ipfix)
            if flow is not None:
                flows.append(flow)
        return flows


    def _flow_from_values(self, values, export_time, uptime_ms, ipfix):
        src = _ip4(values[IE_SRC_V4]) if IE_SRC_V4 in values else (_ip6(values[IE_SRC_V6]) if IE_SRC_V6 in values else "")
        dst = _ip4(values[IE_DST_V4]) if IE_DST_V4 in values else (_ip6(values[IE_DST_V6]) if IE_DST_V6 in values else "")
        if src == "" or dst == "":
            return None
        proto = _uint(values.get(IE_PROTO, b""))
        src_port = _uint(values.get(IE_SRC_PORT, b""))
        dst_port = _uint(values.get(IE_DST_PORT, b""))
        if proto in (1, 58) and IE_ICMP in values and dst_port == 0:
            dst_port = _uint(values[IE_ICMP])  # type * 256 + code, the way the nfdump path stores it
        packets = _uint(values.get(IE_PACKETS, values.get(IE_PACKETS_TOTAL, b"")))
        octets = _uint(values.get(IE_OCTETS, values.get(IE_OCTETS_TOTAL, b"")))
        start, end = self._times(values, export_time, uptime_ms, ipfix)
        return {
            "ts_start_utc": start,
            "ts_end_utc": end,
            "proto": _protocol(proto),
            "src_ip": src,
            "src_port": src_port,
            "dst_ip": dst,
            "dst_port": dst_port,
            "packets": packets,
            "bytes": octets,
            "in_if": _uint(values.get(IE_INGRESS, b"")),
            "out_if": _uint(values.get(IE_EGRESS, b"")),
            "xlat_src_ip": _ip4(values[IE_NAT_SRC_V4]) if IE_NAT_SRC_V4 in values else "0.0.0.0",
            "xlat_dst_ip": _ip4(values[IE_NAT_DST_V4]) if IE_NAT_DST_V4 in values else "0.0.0.0",
        }


    def _times(self, values, export_time, uptime_ms, ipfix):
        if IE_START_MS in values and IE_END_MS in values:
            return _uint(values[IE_START_MS]) / 1000.0, _uint(values[IE_END_MS]) / 1000.0
        if IE_START_S in values and IE_END_S in values:
            return float(_uint(values[IE_START_S])), float(_uint(values[IE_END_S]))
        if not ipfix and IE_FIRST_SWITCHED in values and IE_LAST_SWITCHED in values:
            return (
                self._switched(export_time, uptime_ms, _uint(values[IE_FIRST_SWITCHED])),
                self._switched(export_time, uptime_ms, _uint(values[IE_LAST_SWITCHED])),
            )
        return float(export_time), float(export_time)  # no usable times: the moment the exporter sent it

#!/bin/sh
# Publishes the Pi's throttling flags for node-exporter's textfile collector.
# Run by trafficcam-throttled.service. Arguments, both optional: the directory to
# write to, and seconds between readings (0 writes once and exits).
set -eu

dir=${1:-/dev/shm/trafficcam}
interval=${2:-30}
mkdir -p "$dir"

metrics() {
    # "throttled=0x50005": the low bits are true now, the same bits 16 higher have
    # been true at some point since boot.
    flags=$(vcgencmd get_throttled | cut -d= -f2)
    [ -n "$flags" ] || return 1
    echo "# HELP rpi_throttled A flag from vcgencmd get_throttled: 1 if set."
    echo "# TYPE rpi_throttled gauge"
    bit=0
    for condition in under_voltage frequency_capped throttled soft_temp_limit; do
        echo "rpi_throttled{condition=\"$condition\",when=\"now\"} $(( (flags >> bit) & 1 ))"
        echo "rpi_throttled{condition=\"$condition\",when=\"since_boot\"} $(( (flags >> (bit + 16)) & 1 ))"
        bit=$((bit + 1))
    done
}

while :; do
    # Renamed into place, so node-exporter never reads half a file.
    metrics > "$dir/throttled.prom.tmp"
    mv "$dir/throttled.prom.tmp" "$dir/throttled.prom"
    [ "$interval" != 0 ] || break
    sleep "$interval"
done

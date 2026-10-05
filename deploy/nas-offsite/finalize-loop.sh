#!/bin/sh
set -u

data=/data
incoming="$data/incoming"
snapshots="$data/snapshots"
logs=/logs
lock="$data/.finalize-lock"

mkdir -p "$incoming" "$snapshots" "$logs"

write_capacity() {
    set -- $(df -Pk "$data" | awk 'NR == 2 {print $2, $3, $4, $5}')
    [ "$#" -eq 4 ] || return 0
    total_kib=$1
    used_kib=$2
    available_kib=$3
    used_percent=${4%\%}
    available_percent=$((100 - used_percent))
    printf 'total_kib=%s\nused_kib=%s\navailable_kib=%s\navailable_percent=%s\nupdated_at=%s\n' \
        "$total_kib" "$used_kib" "$available_kib" "$available_percent" "$(date -Iseconds)" \
        > "$incoming/.capacity.next"
    chmod 600 "$incoming/.capacity.next"
    mv -f "$incoming/.capacity.next" "$incoming/.capacity"
}

finalize_once() {
    ready="$incoming/.ready"
    [ -f "$ready" ] || return 0
    stamp=$(sed -n 's/^snapshot=//p' "$ready" | head -n 1)
    expected=$(sed -n 's/^sha256sums=//p' "$ready" | head -n 1)
    case "$stamp" in
        ''|*[!0-9TZ+-]*) return 0 ;;
    esac
    [ "${#expected}" -eq 64 ] || return 0
    actual=$(sha256sum "$incoming/SHA256SUMS" 2>/dev/null | awk '{print $1}')
    [ "$actual" = "$expected" ] || return 0
    [ -d "$snapshots/$stamp" ] && return 0
    (
        cd "$incoming"
        sha256sum -c SHA256SUMS >/dev/null 2>&1
    ) || {
        printf '%s checksum_failed snapshot=%s\n' "$(date -Iseconds)" "$stamp" >> "$logs/readable-finalizer.log"
        return 0
    }
    partial="$snapshots/.partial-$stamp"
    [ ! -e "$partial" ] || return 0
    mkdir -m 700 "$partial"
    cp -al "$incoming/." "$partial/"
    # Transport-control markers belong to the receiver, not to the readable
    # archive.  Keeping any of them would make the archived file set differ
    # from SHA256SUMS even when every payload file is intact.
    rm -f \
        "$partial/.ready" \
        "$partial/.finalized" \
        "$partial/.finalized.next" \
        "$partial/.capacity" \
        "$partial/.capacity.next"
    mv "$partial" "$snapshots/$stamp"
    ln -s "snapshots/$stamp" "$data/current.next"
    mv -Tf "$data/current.next" "$data/current"
    printf 'snapshot=%s\nsha256sums=%s\nfinalized_at=%s\n' \
        "$stamp" "$expected" "$(date -Iseconds)" > "$incoming/.finalized.next"
    chmod 600 "$incoming/.finalized.next"
    mv -f "$incoming/.finalized.next" "$incoming/.finalized"
    printf '%s finalized snapshot=%s\n' "$(date -Iseconds)" "$stamp" >> "$logs/readable-finalizer.log"
}

while true; do
    write_capacity
    if mkdir "$lock" 2>/dev/null; then
        finalize_once
        rmdir "$lock"
    fi
    sleep 20
done

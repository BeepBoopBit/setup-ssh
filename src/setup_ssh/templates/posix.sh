#!/bin/sh
set -eu
umask 077

decode() {
    printf '%s' "$1" | base64 -d 2>/dev/null || printf '%s' "$1" | base64 -D
}
__PAYLOAD__

SSH_DIR="$HOME/.ssh"
GROUP_DIR="$SSH_DIR/setup-ssh/$GROUP"
KEY="$GROUP_DIR/id_ed25519"
BEGIN="# BEGIN setup-ssh $GROUP"
END="# END setup-ssh $GROUP"
WORK=""
trap 'if [ -n "$WORK" ]; then rm -rf "$WORK"; fi' EXIT HUP INT TERM

fail() { printf '%s\n' "$*" >&2; exit 1; }

# Replace only this group's balanced block. Refuse malformed blocks and symlinks.
# Back up changed existing files; atomically replace with a mode-600 file.
managed() {
    target=$1
    body=$2
    position=$3
    [ ! -L "$target" ] || fail "Refusing symlink: $target"
    [ ! -e "$target" ] || [ -f "$target" ] || fail "Not a regular file: $target"
    original="$WORK/original"
    remainder="$WORK/remainder"
    if [ -f "$target" ]; then cat "$target" > "$original"; else : > "$original"; fi
    awk -v begin="$BEGIN" -v end="$END" '
        $0 == begin { if (inside || seen) exit 2; inside=1; seen=1; next }
        $0 == end { if (!inside) exit 2; inside=0; next }
        !inside { print }
        END { if (inside) exit 2 }
    ' "$original" > "$remainder" || fail "Malformed setup-ssh block in $target; restore its backup."
    replacement=$(mktemp "$target.tmp.XXXXXXXX")
    if [ "$position" = prepend ] && [ -n "$body" ]; then
        printf '%s\n%s\n%s\n' "$BEGIN" "$body" "$END" > "$replacement"
        cat "$remainder" >> "$replacement"
    else
        cat "$remainder" > "$replacement"
        if [ -n "$body" ]; then printf '%s\n%s\n%s\n' "$BEGIN" "$body" "$END" >> "$replacement"; fi
    fi
    if [ -f "$target" ] && cmp -s "$target" "$replacement"; then
        rm "$replacement"
        chmod 600 "$target"
    else
        if [ -f "$target" ]; then
            cp -p "$target" "$target.setup-ssh.bak"
            chmod 600 "$target.setup-ssh.bak"
        fi
        chmod 600 "$replacement"
        mv -f "$replacement" "$target"
    fi
}

write_owned() {
    target=$1
    content=$2
    [ ! -L "$target" ] || fail "Refusing symlink: $target"
    if [ -f "$target" ] && [ "$(cat "$target")" = "$content" ]; then return; fi
    replacement=$(mktemp "$target.tmp.XXXXXXXX")
    printf '%s\n' "$content" > "$replacement"
    chmod 600 "$replacement"
    mv -f "$replacement" "$target"
}

case "$ACTION" in
    prepare)
        for tool in ssh ssh-keygen base64 awk mktemp; do
            command -v "$tool" >/dev/null 2>&1 || fail "Missing $tool. Run setup-ssh doctor for instructions."
        done
        mkdir -p "$GROUP_DIR"
        chmod 700 "$SSH_DIR" "$SSH_DIR/setup-ssh" "$GROUP_DIR"
        [ ! -L "$KEY" ] && [ ! -L "$KEY.pub" ] || fail "Refusing a symlinked managed key."
        if [ ! -f "$KEY" ]; then
            [ ! -e "$KEY.pub" ] || fail "Managed private key is missing; restore it. To replace it, remove this device and delete its orphaned .pub file before re-enrolling."
            ssh-keygen -q -t ed25519 -N '' -C "setup-ssh:$GROUP:$DEVICE" -f "$KEY" < /dev/null
        fi
        chmod 600 "$KEY"
        public=$(ssh-keygen -y -P '' -f "$KEY" < /dev/null) || fail "Cannot read managed key; it must be an unencrypted Ed25519 key."
        write_owned "$KEY.pub" "$public setup-ssh:$GROUP:$DEVICE"
        printf 'SETUP_SSH_KEY=%s\n' "$public"
        ;;
    apply|remove)
        mkdir -p "$GROUP_DIR"
        chmod 700 "$SSH_DIR" "$SSH_DIR/setup-ssh" "$GROUP_DIR"
        WORK=$(mktemp -d "$GROUP_DIR/.work.XXXXXXXX")
        if [ "$ACTION" = apply ]; then
            [ -f "$KEY" ] || fail "Managed private key is missing. Run sync."
            write_owned "$GROUP_DIR/config" "$CONFIG"
            write_owned "$GROUP_DIR/known_hosts" "$KNOWN_HOSTS"
            managed "$SSH_DIR/authorized_keys" "$AUTHORIZED_KEYS" append
            managed "$SSH_DIR/config" "Include \"~/.ssh/setup-ssh/$GROUP/config\"" prepend
        else
            managed "$SSH_DIR/authorized_keys" '' append
            managed "$SSH_DIR/config" '' prepend
        fi
        ;;
    check)
        [ -f "$GROUP_DIR/config" ] || fail "No managed SSH configuration. Run sync first."
        exec ssh -F "$GROUP_DIR/config" \
            -o BatchMode=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
            -o PreferredAuthentications=publickey -o IdentityAgent=none \
            -o ConnectionAttempts=1 -o "ConnectTimeout=$TIMEOUT" \
            "$DESTINATION" 'echo setup-ssh-ok'
        ;;
    *) fail "Unknown action: $ACTION" ;;
esac

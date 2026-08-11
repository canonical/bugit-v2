#!/bin/sh

# the following are copilot comments

# Runs the upstream sosreport snap (confinement: classic) instead of a
# vendored copy of sos report inside this (strict/devmode) snap.
#
# Why not just exec /snap/bin/sosreport.sos directly: bugit's own process
# already lives inside a private mount namespace that snap-confine set up
# for it (core24 base remap, our 'layout' entries, hostfs bind mounts,
# etc). snap-confine's 'unshare(CLONE_NEWNS)' for the *child* classic snap
# process only takes a COPY of whatever mount namespace the calling
# process is currently in - so launching sosreport.sos as a normal child
# of bugit still inherits bugit's remapped/confined view, even though
# 'confinement: classic' itself imposes no *extra* restrictions of its
# own. Confinement classic only helps if the process reaches it starting
# from the real, unmodified host mount namespace.
#
# 'nsenter -t 1 -m' joins PID 1's mount namespace, i.e. the *actual* host
# root mount namespace (PID 1 is never itself remapped by any snap), which
# is unaffected by bugit's own confinement. From there, running
# 'sosreport.sos' behaves identically to invoking it directly from a host
# shell: real (writable) host filesystem, real installed packages, real
# hardware tools - exactly what we want for sos report to produce
# equivalent output whether it's called from inside or outside the snap.
# This needs bugit to run as real root (it's always launched via 'sudo',
# see README), since joining another process's namespace needs
# CAP_SYS_ADMIN/ptrace access to /proc/1.
set -e

export HWLOC_COMPONENTS=-gl

if ! [ $(id -u) = 0 ]; then
	echo "You must run this as root"
	exit 1
fi

if ! nsenter -t 1 -m -- which apt 2>&1 > /dev/null; then
	echo "Cannot run sos report in ubuntu core"
	exit 1
fi

if ! nsenter -t 1 -m -- snap list sosreport >/dev/null 2>&1; then
	echo "sosreport snap not found on host, installing it now..." >&2
	nsenter -t 1 -m -- snap install --classic sosreport
fi

exec nsenter -t 1 -m -- /snap/bin/sosreport.sos "$@"

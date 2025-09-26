#!/bin/bash

# this whole thing is here to export the ARCH variable
# originally from checkbox
# variables exported here are accessible everywhere inside bugit-v2
# specifying them in the pipx version is not necessary
case "$SNAP_ARCH" in
"amd64")
	export ARCH='x86_64-linux-gnu'
	;;
"i386")
	export ARCH='i386-linux-gnu'
	;;
"arm64")
	export ARCH='aarch64-linux-gnu'
	;;
"armhf")
	export ARCH='arm-linux-gnueabihf'
	;;
*)
	echo "Unsupported architecture: $SNAP_ARCH"
	;;
esac

# PERL_VERSION=$(perl -e '$^V=~/^v(\d+\.\d+)/;print $1')
# export PERL5LIB="$PERL5LIB:$SNAP/usr/lib/$ARCH/perl/$PERL_VERSION:$SNAP/usr/lib/$ARCH/perl5/$PERL_VERSION:$SNAP/usr/share/perl/$PERL_VERSION:$SNAP/usr/share/perl5"

# https://github.com/snapcrafters/get-iplayer/blob/candidate/snap/local/scripts/launcher
exec "$@"

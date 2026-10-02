#!/bin/sh
set -eu

mkdir -p /tmp/classes
find /workspace -type f -name '*.java' -print0 \
  | xargs -0 -r javac -J-Xmx256m -proc:none -classpath /tmp/classes -sourcepath '' \
      -implicit:none -encoding UTF-8 -d /tmp/classes
exec java -XX:-UsePerfData -Xmx256m -ea -cp /tmp/classes "$1"

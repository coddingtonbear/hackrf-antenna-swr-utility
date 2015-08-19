#!/usr/bin/python
from __future__ import print_function

import argparse
import io
import json
import logging
import signal
import sys
import subprocess

from matplotlib import pyplot


COMMANDS = {}


logger = logging.getLogger(__name__)


def command(fn):
    global COMMANDS
    COMMANDS[fn.__name__] = fn


def get_antenna_statistics(common, start_frequency, stop_frequency):
    if common.use_rtl_power:
        path = common.rtl_power_path
        stats = get_antenna_statistics_rtl_power(
            path, start_frequency, stop_frequency,
        )
    else:
        path = common.osmocom_spectrum_sense_path
        stats = get_antenna_statistics_osmocom(
            path, start_frequency, stop_frequency
        )

    return stats


def get_antenna_statistics_osmocom(
    osmocom_path, start_frequency, stop_frequency, loops=5
):
    logger.info(
        "Scanning power levels for %s to %s",
        start_frequency,
        stop_frequency,
    )
    try:
        # osmocom_spectrum_sense -a hackrf -a repeat=false 750e6 950e6
        proc = subprocess.Popen(
            [
                osmocom_path,
                '--args=hackrf',
                '--gain=0',
                '%se6' % start_frequency,
                '%se6' % stop_frequency,
            ],
            stdout=subprocess.PIPE,
        )

        frequencies = {}
        loop_cursor = 0
        max_encountered_frequency = 0

        while True:
            line = proc.stdout.readline()

            line = line.strip()
            logger.debug(line)
            try:
                # 2015-08-17 20:32:17.464280 center_freq 765000000.0\
                #  freq 767993750.0 power_db 3.53722358601\
                #  noise_floor_db -75.9338252561
                (
                    date, time,
                    _, center_freq,
                    _, freq,
                    _, power_db,
                    _, noise_floor_db
                ) = line.split(' ')
            except:
                # The first few lines out will always be unusable
                continue

            if float(freq) <= max_encountered_frequency:
                loop_cursor += 1
            else:
                max_encountered_frequency = float(freq)

            if loop_cursor >= loops:
                logger.info(
                    "Scanning completed."
                )
                proc.send_signal(signal.SIGINT)
                break

            frequencies.setdefault(float(center_freq), [])\
                .append(float(power_db))
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        raise

    return frequencies


def get_antenna_statistics_rtl_power(
    rtl_power_path, start_frequency, stop_frequency, loops=1
):
    logger.info(
        "Scanning power levels for %s to %s",
        start_frequency,
        stop_frequency,
    )
    try:
        # rtl_power -f 750M:950M:100k
        proc = subprocess.Popen(
            [
                rtl_power_path,
                '-f',
                '%sM:%sM:100k' % (
                    start_frequency,
                    stop_frequency,
                )
            ],
            stdout=subprocess.PIPE,
        )

        frequencies = {}
        loop_cursor = 0
        max_encountered_frequency = 0

        while True:
            line = proc.stdout.readline()

            line = line.strip()
            logger.debug(line)

            if not line:
                break

            try:
                parts = line.split(',')
                base_freq = float(parts[2])
                interval = float(parts[4])
                measurements = parts[6:]
            except:
                logger.exception(
                    'Error encontered while parsing output: %s',
                    line
                )
                continue

            for idx, power in enumerate(measurements):
                freq = base_freq + idx * interval
                if freq <= max_encountered_frequency:
                    loop_cursor += 1
                else:
                    max_encountered_frequency = freq

                if loop_cursor >= loops:
                    logger.info(
                        "Scanning completed."
                    )
                    proc.send_signal(signal.SIGINT)
                    break

                frequencies.setdefault(float(freq), [])\
                    .append(float(power))
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)

    return frequencies


def get_frequency_swr_pairs(baseline, measurements):
    data_pairs = []

    for frequency in sorted(baseline.keys()):
        pre = baseline[frequency]
        post = measurements[float(frequency)]

        pre_value = sum(pre) / float(len(pre))
        post_value = sum(post) / float(len(post))

        loss = max(pre_value - post_value, 0.001)
        swr = (10 ** (loss / 20) + 1) / (10 ** (loss / 20) - 1)

        data_pairs.append((frequency, swr))

    return data_pairs


def display_analysis(data_pairs):
    pyplot.subplot(2, 1, 1)
    pyplot.plot(
        [data[0] for data in data_pairs],
        [data[1] for data in data_pairs],
        color='blue',
        lw=2,
    )
    pyplot.yscale('log')
    pyplot.show()


def read_data_pairs_from_file(path):
    pairs = []

    with io.open(path, 'r') as data:
        for line in data:
            line = line.strip()
            if not line:
                continue

            frequency_str, swr_str = line.split(',')
            pairs.append(
                (float(frequency_str), float(swr_str))
            )

    return pairs


@command
def analyze(common, extra):
    parser = argparse.ArgumentParser()
    parser.add_argument('baseline')
    parser.add_argument('-o', default=None)
    parser.add_argument('--stdout', default=False)
    parser.add_argument('-i', default=None)
    args = parser.parse_args(extra)

    if args.i:
        data_pairs = read_data_pairs_from_file(args.i)
    else:
        with io.open(args.baseline, 'r') as baseline_file:
            baseline_data = json.loads(baseline_file.read())

        statistics = get_antenna_statistics(
            common,
            baseline_data['meta']['start_frequency'],
            baseline_data['meta']['stop_frequency'],
        )
        baseline = baseline_data['frequencies']

        data_pairs = get_frequency_swr_pairs(baseline, statistics)

    if args.o:
        if args.stdout:
            for frequency, swr in data_pairs:
                print("{frequency},{swr}".format(frequency=frequency, swr=swr))
        else:
            with io.open(args.o, 'w') as out:
                for frequency, swr in data_pairs:
                    out.write(
                        "{frequency},{swr}".format(
                            frequency=frequency,
                            swr=swr
                        )
                    )
    else:
        logger.info("Displaying GUI analysis.")
        display_analysis(data_pairs)


@command
def baseline(common, extra):
    parser = argparse.ArgumentParser()
    parser.add_argument('start_frequency', type=int, help='MHz')
    parser.add_argument('stop_frequency', type=int, help='MHz')
    parser.add_argument('-o', default=None)
    args = parser.parse_args(extra)

    statistics = get_antenna_statistics(
        common,
        args.start_frequency,
        args.stop_frequency
    )

    data = {
        'meta': {
            'start_frequency': args.start_frequency,
            'stop_frequency': args.stop_frequency,
        },
        'frequencies': statistics,
    }
    serialized = json.dumps(data, indent=4, sort_keys=True)

    if args.o:
        with io.open(args.o, 'w') as out:
            out.write(unicode(serialized))
    else:
        print(serialized)


def cmdline(args=None):
    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=COMMANDS.keys())
    parser.add_argument(
        '--osmocom-spectrum-sense-path',
        default='/usr/local/bin/osmocom_spectrum_sense',
    )
    parser.add_argument(
        '--rtl-power-path',
        default='/usr/local/bin/rtl_power',
    )
    parser.add_argument(
        '--use-rtl-power',
        dest='use_rtl_power',
        action='store_true',
        default=False,
    )
    parser.add_argument(
        '--loglevel',
        default='INFO'
    )
    args, extra = parser.parse_known_args(args)

    logging.basicConfig(level=getattr(logging, args.loglevel))

    COMMANDS[args.command](args, extra)


if __name__ == '__main__':
    cmdline()

import glob
import csv
import statistics

latencies = []

for fn in glob.glob("latencies_*.csv"):
    with open(fn, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            try:
                latencies.append(float(row[1]))
            except (ValueError, IndexError):
                pass

print("Total samples:", len(latencies))
if latencies:
    print("Average latency (ms):", statistics.mean(latencies))
else:
    print("No data")

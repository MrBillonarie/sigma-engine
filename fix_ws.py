
with open('/opt/sigma/web_server.py', 'r') as f:
    lines = f.readlines()
result = []
i = 0
while i < len(lines):
    line = lines[i]
    if "open_t['direction']=='long') or" in line and i+1 < len(lines):
        next_line = lines[i+1]
        if "regime=='BULL'" in next_line and "direction']=='short'" in next_line:
            # Merge the two lines
            merged = line.rstrip().rstrip('\').rstrip() + ' '
            merged += next_line.lstrip()
            result.append(merged)
            i += 2
            continue
    result.append(line)
    i += 1
with open('/opt/sigma/web_server.py', 'w') as f:
    f.writelines(result)
print("done", len(result), "lines")

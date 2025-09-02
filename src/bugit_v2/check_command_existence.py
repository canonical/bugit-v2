import shutil

commands = ["dmidecode", "lspci", "uname", "oem-getlogs", "find", "tail"]

ok = set[str]()
for c in commands:
    p = shutil.which(c)
    print(c, p)
    if p:
        ok.add(c)

print()
print("exists:", f"{len(ok)}/{len(commands)}")
for c in ok:
    print(" -", c)

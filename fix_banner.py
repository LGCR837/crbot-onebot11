path = r'C:\Users\Administrator\Desktop\crbot-onebot11\main.py'
with open(path, 'rb') as f:
    raw = f.read()

# Line 109 has 1 space indent, needs 4 spaces
# Line 110 is a blank line with 1 space, should be just \n
old_109 = b' logger.info(" %s", banner)\n'
new_109 = b'    logger.info(" %s", banner)\n'

old_110 = b' \n'
new_110 = b'\n'

replaced = False
if old_109 in raw:
    raw = raw.replace(old_109, new_109, 1)
    replaced = True
    print('Fixed line 109')
else:
    print('Line 109 not found')

if old_110 in raw:
    raw = raw.replace(old_110, new_110, 1)
    replaced = True
    print('Fixed line 110')
else:
    print('Line 110 not found')

if replaced:
    with open(path, 'wb') as f:
        f.write(raw)
    print('DONE')
else:
    print('NO CHANGES')

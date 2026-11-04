"""Resume a GDrive archive to an SSH node, retaining failed attempts and hashes.

The short-lived access token arrives on stdin and stays on the client. Transfer
receipts qualify bytes only; input recovery and full Build acceptance are separate.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from contextlib import suppress
from urllib import request

REMOTE = r'''
from pathlib import Path
from datetime import datetime,timezone
import fcntl,hashlib,json,os,stat,sys
c=CONFIG
m=c['metadata'];root=Path(c['directory'])
def require(condition,reason):
 if not condition:raise ValueError(reason)
require(root.is_absolute() and root.resolve()==root,'CLOUD_TARGET_PATH_INVALID')
root.mkdir(parents=True,exist_ok=True)
target=root/m['name'];partial=target.with_name(target.name+'.part')
def regular(p):
 s=p.lstat();require(stat.S_ISREG(s.st_mode) and s.st_nlink==1,'CLOUD_TARGET_NOT_REGULAR')
 return s
def hashes(p):
 sha,md5=hashlib.sha256(),hashlib.md5()
 with p.open('rb') as stream:
  while data:=stream.read(4*1024**2):sha.update(data);md5.update(data)
 return sha.hexdigest(),md5.hexdigest()
size=int(m['size']);expected=c['sha256']
if c['mode']=='inspect':
 if target.exists():
  s=regular(target);sha,md5=hashes(target)
  require(s.st_size==size and sha==expected and md5==m['md5Checksum'],
          'CLOUD_EXISTING_TARGET_CONFLICT')
  print(json.dumps({'status':'VERIFIED_REUSED_CLOUD_BYTES','offset':size,'sha256':sha}))
 else:
  offset=regular(partial).st_size if partial.exists() else 0
  require(0<=offset<=size,'CLOUD_PARTIAL_SIZE_INVALID')
  if offset:
   prior=[root/(m['name']+'.receipt.json'),*root.glob(m['name']+'.download-*.json')]
   failures=[json.loads(p.read_bytes()) for p in prior if p.exists()]
   require(any(r.get('id')==m['id'] and r.get('expected_sha256')==expected and
               r.get('status')=='FAILED_PARTIAL_PRESERVED' for r in failures),
           'CLOUD_PARTIAL_WITHOUT_FAILURE_EVIDENCE')
  print(json.dumps({'status':'TRANSFER_REQUIRED','offset':offset}))
 raise SystemExit(0)
lock=(root/('.'+m['name']+'.download.lock')).open('a+')
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
require(not target.exists() and not target.is_symlink(),'CLOUD_TARGET_APPEARED')
offset=c['offset']
require((regular(partial).st_size if partial.exists() else 0)==offset,'CLOUD_PARTIAL_CHANGED')
sha,md5,count=hashlib.sha256(),hashlib.md5(),0
if offset:
 with partial.open('rb') as stream:
  while data:=stream.read(4*1024**2):sha.update(data);md5.update(data);count+=len(data)
prefix_sha=sha.hexdigest()
with partial.open('ab' if partial.exists() else 'xb') as stream:
 while data:=sys.stdin.buffer.read(4*1024**2):
  require(count+len(data)<=size,'CLOUD_RESPONSE_TOO_LARGE')
  stream.write(data);sha.update(data);md5.update(data);count+=len(data)
 stream.flush();os.fsync(stream.fileno())
passed=count==size and sha.hexdigest()==expected and md5.hexdigest()==m['md5Checksum']
receipt={'source':'GDRIVE_API_MEDIA_RANGE','id':m['id'],'name':m['name'],
 'offset':offset,'resumed_bytes':count-offset,'bytes':count,'sha256':sha.hexdigest(),
 'md5':md5.hexdigest(),'expected_sha256':expected,'prefix_sha256':prefix_sha,
 'finished_at':datetime.now(timezone.utc).isoformat(),
 'status':'VERIFIED_CLOUD_BYTES' if passed else 'FAILED_PARTIAL_PRESERVED',
 'formal_READY':False}
if passed:
 require(not target.exists() and not target.is_symlink(),'CLOUD_TARGET_APPEARED')
 partial.rename(target)
name=m['name']+'.download-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json'
with (root/name).open('x') as stream:json.dump(receipt,stream,indent=2);stream.write('\n')
print(json.dumps(receipt),flush=True)
raise SystemExit(0 if passed else 1)
'''


def command(host, config):
    source = REMOTE.replace("CONFIG", repr(config), 1)
    compile(source, "<cloud-download-receiver>", "exec")
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
            "python3 -c " + shlex.quote(source)]


def download(*, file_id, sha256, host, directory, token, proxy=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", file_id) or not re.fullmatch(
        r"[0-9a-f]{64}", sha256
    ) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", host):
        raise ValueError("CLOUD_DOWNLOAD_IDENTITY_INVALID")
    if not token or "\n" in token or "\r" in token:
        raise ValueError("CLOUD_DOWNLOAD_TOKEN_INVALID")
    opener = request.build_opener(
        request.ProxyHandler({"https": proxy, "http": proxy})
    ) if proxy else request.build_opener()
    url = "https://www.googleapis.com/drive/v3/files/" + file_id
    headers = {"Authorization": "Bearer " + token}
    with opener.open(request.Request(
        url + "?fields=id,name,size,md5Checksum,sha256Checksum", headers=headers
    ), timeout=30) as response:
        metadata = json.load(response)
    name = metadata["name"]
    if (metadata["id"] != file_id or name in {"", ".", ".."}
            or any(c in name for c in "/\\\r\n\x00")
            or metadata.get("sha256Checksum", sha256) != sha256):
        raise ValueError("CLOUD_DOWNLOAD_METADATA_CHANGED")
    config = {"metadata": metadata, "sha256": sha256, "directory": directory, "mode": "inspect"}
    inspected = json.loads(subprocess.check_output(command(host, config), text=True))
    if inspected["status"] == "VERIFIED_REUSED_CLOUD_BYTES":
        return inspected
    offset = inspected["offset"]
    response = None
    if offset < int(metadata["size"]):
        if offset:
            headers["Range"] = "bytes=" + str(offset) + "-"
        response = opener.open(request.Request(url + "?alt=media", headers=headers), timeout=120)
        if offset and (response.status != 206 or response.headers.get("Content-Range") !=
                       f"bytes {offset}-{int(metadata['size'])-1}/{metadata['size']}"):
            response.close()
            raise ValueError("CLOUD_DOWNLOAD_RANGE_NOT_ACKNOWLEDGED")
    config.update(mode="receive", offset=offset)
    sink = subprocess.Popen(command(host, config), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    network_error = None
    try:
        if response:
            while block := response.read(4 * 1024**2):
                sink.stdin.write(block)
    except Exception as error:
        network_error = type(error).__name__
    finally:
        if response:
            response.close()
        with suppress(BrokenPipeError):
            sink.stdin.close()
    stdout, stderr = sink.stdout.read(), sink.stderr.read()
    code = sink.wait()
    if code:
        # Original and resumed partials and their distinct receipts remain.
        raise RuntimeError("CLOUD_DOWNLOAD_FAILED_PARTIAL_PRESERVED: "
                           + (network_error or stderr.decode()[-1500:] or stdout.decode()))
    return json.loads(stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("file-id", "sha256", "host", "directory"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--proxy")
    args = parser.parse_args()
    import sys

    token = json.load(sys.stdin)["access_token"]
    print(json.dumps(download(file_id=args.file_id, sha256=args.sha256, host=args.host,
                              directory=args.directory, token=token, proxy=args.proxy)))


if __name__ == "__main__":
    main()

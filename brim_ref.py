#!/usr/bin/env python3
"""Independent BRIM/BRPV/BRTM parser/verifier for BR-480.
This implementation intentionally does not import or bind the production C code.
"""
from __future__ import annotations
import argparse, hashlib, json, struct, sys
from pathlib import Path
from dataclasses import dataclass, asdict
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except Exception:
    Ed25519PublicKey=None

HEADER=80; INSN=16; PROV=96; TRUST=416
ISA_MAJOR=4; ISA_MINOR=1; ABI_MAJOR=1; IMAGE_ABI=1
REGS=16; CAPS_REGS=16; OPCODES=32; MODES=4; MAX_CODE=256; MAX_DATA=4096
CAP_ALL=0xff
OPS=['NOP','MOVI','MOV','JMP','JZ','JNZ','HALT','ADD','SUB','MUL','DIVU','MODU','AND','OR','XOR','NOT','SHL','SHR','CMP','LOAD','STORE','PUSH','POP','SVC','CALL','RET','YIELD','TRAP','CAPQ','CHECKPOINT','LDX','STX']
CONTROL={0,1,2,3,4,5,6,24,25,26,27,28}; ARITH=set(range(7,19)); MEMORY={19,20,30,31}; STACK={21,22}
SVC=23; CHECKPOINT=29
CAP_CONTROL=1; CAP_ARITH=2; CAP_MEMORY=4; CAP_STACK=8; CAP_SERVICE=16; CAP_STATE=32; CAP_UPDATE=64; CAP_DIAG=128
SVC_CAPS=[CAP_SERVICE,CAP_SERVICE,CAP_SERVICE,CAP_SERVICE,CAP_SERVICE|CAP_STATE,CAP_SERVICE|CAP_STATE,CAP_SERVICE|CAP_DIAG,CAP_SERVICE,CAP_SERVICE|CAP_UPDATE,CAP_SERVICE,CAP_SERVICE,CAP_SERVICE,CAP_SERVICE|CAP_STATE,CAP_SERVICE|CAP_STATE,CAP_SERVICE,CAP_SERVICE|CAP_STATE,CAP_SERVICE,CAP_SERVICE|CAP_DIAG,CAP_SERVICE,CAP_SERVICE]
ROOT=bytes.fromhex('78c64f98e482099e5fc21131d7e70a784b2f59e9243a42602ff4a70fb665f22d')

def u16(b,o): return struct.unpack_from('<H',b,o)[0]
def u32(b,o): return struct.unpack_from('<I',b,o)[0]
def u64(b,o): return struct.unpack_from('<Q',b,o)[0]
def sha(b): return hashlib.sha256(b).digest()
def kid(k): return u64(sha(k),0)
class VerifyError(ValueError): pass

def fail(msg): raise VerifyError(msg)

def opcap(op,imm):
    if op in CONTROL: return CAP_CONTROL
    if op in ARITH: return CAP_ARITH
    if op in MEMORY: return CAP_MEMORY
    if op in STACK: return CAP_STACK
    if op==CHECKPOINT: return CAP_STATE
    if op==SVC:
        if imm>=len(SVC_CAPS): fail('service id')
        return SVC_CAPS[imm]
    fail('opcode capability')

def feat(op):
    x=0
    if op in (24,25): x|=1
    if op in (30,31): x|=2
    if 26<=op<=29: x|=4
    return x

@dataclass
class Brim:
    bytes:int; isa_major:int; isa_minor:int; abi_major:int; flags:int; image_abi:int; image_version:int
    requested_caps:int; ext_bytes:int; code_count:int; data_bytes:int; payload_sha256:str; derived_caps:int
    max_steps:int; feature_word:int; source_sha256:str; brir_sha256:str; instructions:list

def parse_brim(blob:bytes, verify=True)->Brim:
    if len(blob)<HEADER: fail('truncated header')
    if blob[:4]!=b'BRIM': fail('magic')
    if blob[4]!=ISA_MAJOR or not (1<=blob[5]<=ISA_MINOR): fail('ISA')
    if blob[6]!=ABI_MAJOR: fail('ABI')
    if blob[8]!=IMAGE_ABI: fail('image ABI')
    if blob[9]!=20 or blob[10]!=REGS or blob[11]!=CAPS_REGS or blob[12]!=OPCODES or blob[13]!=MODES: fail('geometry')
    if u16(blob,14)!=HEADER: fail('header bytes')
    flags=blob[7]
    if flags & ~0x07: fail('flags')
    version=u32(blob,16); req=u32(blob,20); ext=u32(blob,24); cc=u16(blob,28); dl=u16(blob,30)
    if not version or not req or req&~CAP_ALL or not cc or cc>MAX_CODE or dl>MAX_DATA: fail('header bounds')
    if not (flags&0x04) or ext!=PROV: fail('provenance required')
    payload=cc*INSN+dl+ext
    raw=HEADER+payload
    signed=bool(flags&0x02)
    expected=raw+(64 if signed else 0)
    if len(blob)!=expected: fail('length')
    if sha(blob[HEADER:HEADER+payload])!=blob[32:64]: fail('payload hash')
    poff=HEADER+cc*INSN+dl
    pv=blob[poff:poff+PROV]
    if pv[:4]!=b'BRPV' or pv[4:8]!=bytes([1,1,1,ISA_MAJOR]): fail('BRPV version')
    if u32(pv,8)!=0x00040700 or u32(pv,12)!=0x00040700: fail('authority ABI')
    max_steps=u32(pv,16); derived=u32(pv,20); pver=u32(pv,88); fword=u32(pv,92)
    if not max_steps or pver!=version: fail('BRPV authority')
    ins=[]; need=0; features=8|16|32; succ=[[] for _ in range(cc)]; ds=[0]*cc; cs=[0]*cc
    for i in range(cc):
        p=blob[HEADER+i*INSN:HEADER+(i+1)*INSN]
        op,mode,rd,ra,rb,cap=p[:6]; fl=u16(p,6); imm=u64(p,8)
        if op>=OPCODES or mode>=MODES or rd>=REGS or ra>=REGS or rb>=REGS or cap!=0 or fl: fail(f'instruction {i}')
        if op in (3,4,5,24,25) and imm>=cc: fail(f'control target {i}')
        if op in (19,20) and (imm>MAX_DATA-8 or imm&7): fail(f'memory {i}')
        if op in (30,31) and imm>0xffffffff: fail(f'indexed memory {i}')
        if op in (16,17) and imm>=1048576: fail(f'shift {i}')
        if op==23 and imm>=20: fail(f'service {i}')
        if op==28 and imm>=16: fail(f'capq {i}')
        if op==25:
            if not any(blob[HEADER+k*INSN]==24 and k+1==imm for k in range(cc)): fail(f'RET site {i}')
        r=opcap(op,imm); need|=r; features|=feat(op)
        ds[i]=1 if op==21 else -1 if op==22 else 0
        cs[i]=1 if op==24 else -1 if op==25 else 0
        if op not in (6,27):
            if op in (3,24,25): succ[i]=[imm]
            elif op in (4,5):
                succ[i]=[imm] + ([i+1] if i+1<cc else [])
            elif i+1<cc: succ[i]=[i+1]
            else: fail(f'fallthrough {i}')
        ins.append({'index':i,'op':OPS[op],'mode':mode,'rd':rd,'ra':ra,'rb':rb,'cap':cap,'imm':imm,'need':r,'succ':succ[i]})
    # CFG/stack verification
    depth=[None]*cc; cdepth=[None]*cc; depth[0]=0; cdepth[0]=0; q=[0]; reach={0}
    while q:
        i=q.pop(0); nd=depth[i]+ds[i]; nc=cdepth[i]+cs[i]
        if not (0<=nd<=256 and 0<=nc<=64): fail('stack bounds')
        for v in succ[i]:
            if depth[v] is None:
                depth[v]=nd; cdepth[v]=nc; reach.add(v); q.append(v)
            elif depth[v]!=nd or cdepth[v]!=nc: fail('stack merge')
    if len(reach)!=cc: fail('unreachable code')
    terminal={i for i,x in enumerate(ins) if x['op'] in ('HALT','TRAP')}
    changed=True
    while changed:
        changed=False
        for i in range(cc):
            if i not in terminal and any(v in terminal for v in succ[i]): terminal.add(i); changed=True
    if len(terminal)!=cc: fail('nonterminating CFG')
    if need!=req or derived!=need: fail('capability derivation')
    expected_feature=features | (ISA_MINOR<<16) | (ABI_MAJOR<<24)
    if fword!=expected_feature: fail('feature word')
    return Brim(len(blob),blob[4],blob[5],blob[6],flags,blob[8],version,req,ext,cc,dl,blob[32:64].hex(),derived,max_steps,fword,pv[24:56].hex(),pv[56:88].hex(),ins)

def parse_brir(text:str):
    lines=[x.rstrip('\n') for x in text.splitlines()]
    if len(lines)<3 or lines[0]!='BRIR/1.1': fail('BRIR version')
    meta={}
    for x in lines[1].split('|'):
        if '=' not in x: fail('BRIR metadata')
        k,v=x.split('=',1); meta[k]=v
    required={'unit','language','isa','abi','features','image','max_steps','declared','derived'}
    if set(meta)!=required: fail('BRIR metadata keys')
    rows=[]
    for line in lines[2:]:
        p=line.split('|')
        if len(p)!=14: fail('BRIR columns')
        if int(p[0])!=len(rows): fail('BRIR index')
        if p[2] not in OPS: fail('BRIR opcode')
        rows.append(p)
    return {'meta':meta,'rows':len(rows),'sha256':hashlib.sha256(text.encode()).hexdigest()}

def parse_secure(blob:bytes, root=ROOT):
    # raw BRIM length follows its header. Secure production images never carry legacy 64-byte image signature.
    if len(blob)<HEADER+TRUST: fail('secure length')
    cc=u16(blob,28); dl=u16(blob,30); ext=u32(blob,24); raw=HEADER+cc*INSN+dl+ext
    if raw+TRUST!=len(blob): fail('secure exact length')
    brim=parse_brim(blob[:raw])
    t=blob[raw:]
    if t[:4]!=b'BRTM' or t[4]!=1 or t[5]!=1 or u16(t,6)&~1: fail('BRTM header')
    epoch,gen,tx,iv,caps=u32(t,8),u32(t,12),u32(t,16),u32(t,20),u32(t,24)
    expiry=u64(t,28); rid,iid,lid=u64(t,36),u64(t,44),u64(t,52)
    if not all((epoch,gen,tx,iv,caps,iid,lid)): fail('BRTM required fields')
    if iv!=brim.image_version or caps!=brim.requested_caps: fail('BRTM image binding')
    if rid!=kid(root): fail('root id')
    issuer=t[156:188]; release=t[188:220]
    if iid!=kid(issuer) or lid!=kid(release): fail('key ids')
    raw_hash=sha(blob[:raw])
    if raw_hash!=t[124:156]: fail('BRIM hash binding')
    if bytes.fromhex(brim.source_sha256)!=t[60:92] or bytes.fromhex(brim.brir_sha256)!=t[92:124]: fail('source/BRIR binding')
    if Ed25519PublicKey is None: fail('cryptography unavailable')
    m1=bytearray(80);m1[:15]=b'BR440:ISSUER:v1';struct.pack_into('<I',m1,16,t[5]);m1[20:28]=t[36:44];m1[28:36]=t[44:52];m1[36:40]=t[8:12];m1[40:72]=issuer
    m2=bytearray(80);m2[:16]=b'BR440:RELEASE:v1';struct.pack_into('<I',m2,16,t[5]);m2[20:28]=t[44:52];m2[28:36]=t[52:60];m2[36:40]=t[8:12];m2[40:72]=release
    m3=bytearray(240);m3[:14]=b'BR440:IMAGE:v1';m3[16:240]=t[:224]
    try:
        Ed25519PublicKey.from_public_bytes(root).verify(t[224:288],bytes(m1))
        Ed25519PublicKey.from_public_bytes(issuer).verify(t[288:352],bytes(m2))
        Ed25519PublicKey.from_public_bytes(release).verify(t[352:416],bytes(m3))
    except Exception as e: fail('signature chain')
    return {'brim':asdict(brim),'trust':{'policy':t[5],'flags':u16(t,6),'issuer_epoch':epoch,'generation':gen,'txseq':tx,'image_version':iv,'caps':caps,'expiry':expiry,'root_id':rid,'issuer_id':iid,'release_id':lid,'signature_chain':'PASS'}}

def cli():
    ap=argparse.ArgumentParser(); ap.add_argument('path'); ap.add_argument('--source'); ap.add_argument('--brir'); ap.add_argument('--json',action='store_true'); a=ap.parse_args()
    p=Path(a.path); b=p.read_bytes()
    try:
        r=parse_secure(b) if len(b)>=HEADER+TRUST and b[-TRUST:-TRUST+4]==b'BRTM' else asdict(parse_brim(b))
        image = r['brim'] if isinstance(r,dict) and 'brim' in r else r
        extra={}
        if a.source:
            sh=hashlib.sha256(Path(a.source).read_bytes()).hexdigest()
            if sh!=image['source_sha256']: fail('source hash mismatch')
            extra['source_sha256']=sh
        if a.brir:
            br=parse_brir(Path(a.brir).read_text())
            if br['sha256']!=image['brir_sha256']: fail('BRIR hash mismatch')
            extra['brir']=br
        if extra: r={'image':r,**extra}
        print(json.dumps(r,sort_keys=True) if a.json else 'independent verifier: PASS')
    except Exception as e:
        print(f'independent verifier: FAIL — {e}',file=sys.stderr); return 1
    return 0
if __name__=='__main__': raise SystemExit(cli())

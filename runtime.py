"""Restricted, offline BRIM execution adapter for VIVIAN's LCTL agent policies.

The unmodified BottleRocket 5.0.1 RC native compiler is the language authority.
This module does not compile LCTL. It loads compiler-produced BRIM, verifies it
with the scaffold's independent verifier, and executes only the declared pure
CONTROL/ARITH subset. It is not the production signed-image/secure-boot host.

The VM's unsigned WIDE arithmetic is 1,048,576 bits. This host admits unsigned
64-bit inputs and rejects any intermediate result exceeding 256 bits, well
inside WIDE. Underflow in WRAP/CHECKED therefore fails the host bound; SATURATE
underflow produces zero and TRAPPING underflow traps, as specified by BRVM.
No source, model text, or document text is evaluated as Python or shell code.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable, Mapping, Sequence

try:
    from .brim_ref import parse_brim, VerifyError
except ImportError:
    from brim_ref import parse_brim, VerifyError

WIDE_BITS = 1_048_576
MAX_INPUT = (1 << 64) - 1
MAX_VALUE_BITS = 256
MAX_STEPS = 4096
MAX_SOURCE_BYTES = 512 * 512
MAX_IMAGE_BYTES = 80 + 256 * 16 + 4096 + 96 + 64
ALLOWED_OPS = frozenset({
    'NOP', 'MOVI', 'MOV', 'JMP', 'JZ', 'JNZ', 'HALT',
    'ADD', 'SUB', 'MUL', 'DIVU', 'MODU', 'AND', 'OR', 'XOR',
    'SHL', 'SHR', 'CMP',
})


class PolicyError(ValueError):
    """Invalid policy, input, or bounded execution failure."""


class PolicyCancelled(PolicyError):
    """Execution cancelled by its host."""


@dataclass(frozen=True)
class Instruction:
    op: str
    mode: int
    rd: int
    ra: int
    rb: int
    imm: int


@dataclass(frozen=True)
class Program:
    source_path: str
    image_path: str
    source_sha256: str
    image_sha256: str
    instructions: tuple[Instruction, ...]
    max_steps: int
    requested_caps: int


def _bounded_read(path: Path, maximum: int) -> bytes:
    with path.open('rb') as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise PolicyError(f'File exceeds host resource bound: {path.name}')
    return data


def load_program(source_path: str | Path, image_path: str | Path | None = None,
                 brir_path: str | Path | None = None) -> Program:
    """Verify an LCTL source/BRIM pair and admit only pure bounded policies.

    If a sibling .brir exists, its exact byte hash is also bound to the image.
    Source changes require recompilation by the native BottleRocket compiler.
    Cryptographic hashes detect mismatch; these local policies are unsigned.
    """
    source_path = Path(source_path)
    image_path = Path(image_path) if image_path is not None else source_path.with_suffix('.brimg')
    try:
        source = _bounded_read(source_path, MAX_SOURCE_BYTES)
        blob = _bounded_read(image_path, MAX_IMAGE_BYTES)
        parsed = parse_brim(blob)
    except (OSError, VerifyError, IndexError, OverflowError) as exc:
        raise PolicyError(f'Policy verification failed: {exc}') from exc
    if not source.startswith(b'LCTLC/1.1\n') or b'\r' in source:
        raise PolicyError('Source must be native LCTLC/1.1 with LF line endings')
    source_hash = sha256(source).hexdigest()
    if source_hash != parsed.source_sha256:
        raise PolicyError('LCTL source hash does not match compiled BRIM')
    require_brir = brir_path is not None
    brir_path = Path(brir_path) if require_brir else image_path.with_suffix('.brir')
    try:
        if require_brir or brir_path.is_file():
            if sha256(_bounded_read(brir_path, MAX_SOURCE_BYTES)).hexdigest() != parsed.brir_sha256:
                raise PolicyError('BRIR hash does not match compiled BRIM')
    except OSError as exc:
        raise PolicyError(f'Cannot read BRIR: {exc}') from exc
    if parsed.flags & 2:
        raise PolicyError('This adapter accepts only unsigned local policies; use the native secure host for signatures')
    if parsed.requested_caps & ~3 or parsed.data_bytes:
        raise PolicyError('Only pure CONTROL/ARITH policies without data segments are admitted')
    if not 1 <= parsed.max_steps <= MAX_STEPS:
        raise PolicyError(f'Policy max_steps exceeds host limit {MAX_STEPS}')
    instructions = []
    for row in parsed.instructions:
        if row['op'] not in ALLOWED_OPS:
            raise PolicyError(f"Unsupported instruction for this host: {row['op']}")
        if row['op'] in ('SHL', 'SHR') and row['imm'] > MAX_VALUE_BITS:
            raise PolicyError('Shift exceeds bounded policy profile')
        instructions.append(Instruction(*(row[key] for key in ('op', 'mode', 'rd', 'ra', 'rb', 'imm'))))
    return Program(str(source_path), str(image_path), source_hash,
                   sha256(blob).hexdigest(), tuple(instructions),
                   parsed.max_steps, parsed.requested_caps)


def run_program(program: Program, registers: Mapping[int, int] | Sequence[int] | None = None,
                check_cancelled: Callable[[], object] | None = None) -> dict:
    """Execute a verified image with a trace; cancellation is checked per step.

    check_cancelled may raise the host's cancellation exception or return True.
    CMP changes comparison flags and leaves its syntactic output unchanged.
    JZ/JNZ test that comparison flag; arithmetic does not replace it.
    """
    if not isinstance(program, Program):
        raise PolicyError('Expected a verified Program from load_program')
    regs = [0] * 16
    values = registers.items() if isinstance(registers, Mapping) else enumerate(registers or ())
    for index, value in values:
        if type(index) is not int or not 0 <= index < 16:
            raise PolicyError('Input register index must be an integer from 0 to 15')
        if type(value) is not int or not 0 <= value <= MAX_INPUT:
            raise PolicyError('Input register value must be an unsigned 64-bit integer')
        regs[index] = value
    pc = 0
    flags = 0  # ZERO=1 LESS=2 GREATER=4 CARRY=8 OVERFLOW=16
    trace = []
    for step in range(1, min(program.max_steps, MAX_STEPS) + 1):
        if check_cancelled is not None and check_cancelled():
            raise PolicyCancelled('Policy execution cancelled')
        if not 0 <= pc < len(program.instructions):
            raise PolicyError('Program counter left the verified image')
        ins = program.instructions[pc]
        op, mode = ins.op, ins.mode
        if op not in ALLOWED_OPS:
            raise PolicyError(f'Unsupported opcode: {op}')
        a, b = regs[ins.ra], regs[ins.rb]
        next_pc = pc + 1
        value = None
        before = regs[ins.rd]
        if op == 'MOVI': value = ins.imm
        elif op == 'MOV': value = a
        elif op == 'ADD': value = a + b
        elif op == 'SUB': value = a - b
        elif op == 'MUL': value = a * b
        elif op in ('DIVU', 'MODU'):
            if not b: raise PolicyError(f'Division by zero at instruction {pc}')
            value = a // b if op == 'DIVU' else a % b
        elif op == 'AND': value = a & b
        elif op == 'OR': value = a | b
        elif op == 'XOR': value = a ^ b
        elif op == 'SHL': value = a << ins.imm
        elif op == 'SHR': value = a >> ins.imm
        elif op == 'CMP': flags = (flags & ~7) | (1 if a == b else 2 if a < b else 4)
        elif op == 'JMP': next_pc = ins.imm
        elif op == 'JZ' and flags & 1: next_pc = ins.imm
        elif op == 'JNZ' and not flags & 1: next_pc = ins.imm
        if op in ('ADD', 'SUB', 'MUL', 'SHL', 'SHR'):
            underflow = value < 0
            overflow = value.bit_length() > WIDE_BITS
            flags &= ~(8 | 16)
            if underflow or overflow: flags |= 16
            if (op == 'SUB' and not underflow) or (op != 'SUB' and overflow): flags |= 8
            if underflow or overflow:
                if mode == 3:
                    raise PolicyError(f'BRVM arithmetic trap at instruction {pc}')
                if mode == 2 and underflow:
                    value = 0
                else:
                    raise PolicyError(f'WIDE result exceeds bounded host profile at instruction {pc}')
        if value is not None:
            if not 0 <= value or value.bit_length() > MAX_VALUE_BITS:
                raise PolicyError(f'Register magnitude exceeds {MAX_VALUE_BITS}-bit host bound at instruction {pc}')
            regs[ins.rd] = value
        event = {'step': step, 'pc': pc, 'op': op, 'next_pc': next_pc, 'flags': flags}
        if value is not None:
            event.update(register=ins.rd, before=before, after=value)
        trace.append(event)
        if op == 'HALT':
            return {'registers': regs, 'steps': step, 'trace': trace, 'flags': flags,
                    'halted': True, 'status': 'HALTED'}
        pc = next_pc
    raise PolicyError(f'Policy exhausted its {min(program.max_steps, MAX_STEPS)}-step budget')

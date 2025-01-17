from typing import Self, NamedTuple, Optional
from functools import partial
from collections import defaultdict
import re
from dataclasses import dataclass, field
import cmath
import math

from basis import Position, Chart, JudgeLine, NoteType, Note
from bamboo import LivingBamboo
from rpe import RPE_EASING_FUNCS


PEC_NOTE_TYPES = [NoteType.UNKNOWN, NoteType.TAP, NoteType.HOLD, NoteType.FLICK, NoteType.DRAG]


@dataclass
class PecNote:
    type: NoteType
    time: float
    position_x: float
    speed: float
    scale: float
    above: bool
    end_time: float | None = None

    def sp(self, speed: float) -> Self:
        self.speed = speed
        return self

    def sc(self, scale: float) -> Self:
        self.scale = scale
        return self

    def to_note(self) -> Note:
        return Note(self.type, self.time, (self.end_time or self.time) - self.time, self.position_x)


@dataclass
class PecJudgeLine(JudgeLine):
    pec_notes: list[PecNote] | None = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    angle: LivingBamboo[float] = field(default_factory=LivingBamboo)
    position: LivingBamboo[Position] = field(default_factory=LivingBamboo)
    chart: Optional['PecChart'] = None

    def beat_duration(self, seconds: float) -> float:
        if self.chart:
            for time_start, _, bps in reversed(self.chart.bpss):
                if time_start <= seconds:
                    return 1 / bps
        return 1.875 / 175

    def pos(self, seconds: float, offset: Position) -> Position:
        angle = self.angle @ seconds
        pos = self.position @ seconds
        return pos + cmath.exp(angle * 1j) * offset

    def convert_notes(self) -> None:
        if self.pec_notes is not None:
            self.notes = [pec_note.to_note() for pec_note in self.pec_notes]
            del self.pec_notes


class PecBpsInfo(NamedTuple):
    time: float  # seconds
    beats: float  # how many beats have passed
    bps: float  # beats per second


@dataclass
class PecChart(Chart):
    offset: float
    bpss: list[PecBpsInfo]
    judge_lines: defaultdict[int, PecJudgeLine]

    _CHART_WIDTH = 2048
    _CHART_HEIGHT = 1400

    def __init__(self, content: str, ratio: tuple[int, int]):
        super().__init__()

        self.width, self.height = ratio

        self.offset = 0
        self.bpss = []
        self.judge_lines = defaultdict(lambda: PecJudgeLine(chart=self))

        # 将pec格式的内容转换为python代码，让python解释器帮助我们解析执行
        content = re.sub(r'''["'+eghijkloqstuwxyzA-Z*/\\]''', '', content)  # 避免不必要的麻烦
        content = (
            '\n'.join(
                re.sub(r'\s+', ' ', line.strip()).replace(' ', '(', 1).replace(' ', ',') + ')'
                for line in content.splitlines()
                if line
            )
            .replace('\n#', '.sp')
            .replace('\n&', '.sc')
        )

        # 调用个exec来帮助我们解析转换完的pec格式
        global_vars = {
            name: getattr(self, '_' + name) for name in ['off', 'bp', 'cv', 'cp', 'cd', 'ca', 'cm', 'cr', 'cf']
        } | {'__builtins__': {}}
        for i in range(1, 5):
            global_vars[f'n{i}'] = partial(self._note, i)
        exec(
            'off(' + content,
            global_vars,
            {},
        )

        self.lines = []
        for line in self.judge_lines.values():
            line.convert_notes()
            self.lines.append(line)

    def _note(self, note_type: int, line_number: int, *args) -> PecNote:
        # the tap note
        note_type_enum = PEC_NOTE_TYPES[note_type]
        if note_type_enum == NoteType.HOLD:
            start_beats, end_beats, position_x, above, fake = args
            start = self._beats_to_seconds(start_beats)
            end = self._beats_to_seconds(end_beats)
        else:
            beats, position_x, above, fake = args
            start = self._beats_to_seconds(beats)
            end = None
        note = PecNote(note_type_enum, start, position_x / self._CHART_WIDTH * self.width, 1.0, 1.0, bool(above), end)
        if not fake:
            pec_notes = self.judge_lines[line_number].pec_notes
            assert pec_notes is not None
            pec_notes.append(note)
        return note

    def _off(self, offset: int) -> None:
        # TODO: why?
        self.offset = offset / 1000 - 0.15

    def _bp(self, beats: float, bpm: float) -> None:
        bps = bpm / 60
        if not self.bpss:
            self.bpss.append(PecBpsInfo(0, beats, bps))
            return
        seconds_passed, beats_passed, last_bps = self.bpss[-1]
        seconds_passed += (beats - beats_passed) / last_bps
        self.bpss.append(PecBpsInfo(seconds_passed, beats, bps))

    def _beats_to_seconds(self, beats: float) -> float:
        # 通常来讲，bpm事件列表的长度一般小于100
        # 所以理论上逆序遍历足够了
        # 应该没有必要设计高级的数据结构
        # 再说了，都用python了那还要啥自行车
        for seconds, beats_begin, bps in reversed(self.bpss):
            if beats >= beats_begin:
                return seconds + (beats - beats_begin) / bps
        raise RuntimeError('???')

    def _cv(self, line_number: int, beats: float, speed: float) -> None:
        # ignore speed event
        pass

    def _cp(self, line_number: int, beats: float, x: float, y: float) -> None:
        # set position
        x = x / self._CHART_WIDTH * self.width
        y = y / self._CHART_HEIGHT * self.height
        seconds = self._beats_to_seconds(beats)
        self.judge_lines[line_number].position.cut(seconds, complex(x, self.height - y))

    def _cd(self, line_number: int, beats: float, degree: float) -> None:
        # set degree
        seconds = self._beats_to_seconds(beats)
        self.judge_lines[line_number].angle.cut(seconds, math.radians(degree))

    def _ca(self, line_number: int, beats: float, opacity: float) -> None:
        # ignore opacity setting event
        pass

    def _cm(self, line_number: int, start_beats: float, end_beats: float, x: float, y: float, easing_type: int) -> None:
        # motion event
        x = x / self._CHART_WIDTH * self.width
        y = y / self._CHART_HEIGHT * self.height
        seconds_start = self._beats_to_seconds(start_beats)
        seconds_end = self._beats_to_seconds(end_beats)
        self.judge_lines[line_number].position.embed(
            seconds_start, seconds_end, complex(x, self.height - y), RPE_EASING_FUNCS[easing_type]
        )

    def _cr(self, line_number: int, start_beats: float, end_beats: float, end: float, easing_type: int) -> None:
        # rotate event
        seconds_start = self._beats_to_seconds(start_beats)
        seconds_end = self._beats_to_seconds(end_beats)
        line = self.judge_lines[line_number]
        line.angle.embed(seconds_start, seconds_end, math.radians(end), RPE_EASING_FUNCS[easing_type])

    def _cf(self, line_number: int, start_beats: float, end_beats: float, end: float) -> None:
        # ignore opacity setting event
        pass

__all__ = ['PecChart']
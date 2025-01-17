from typing import NamedTuple, TypeVar, Generic, Protocol, runtime_checkable, Callable
from abc import ABCMeta, abstractmethod
import bisect
import math

from easing import EasingFunction, LVALUE

# 泛型约束：可以插值（跟自己相加减，跟浮点数相乘除可以得到同类型结果）

S = TypeVar('S', bound='_Interpable')


@runtime_checkable
class _Interpable(Protocol):
    @abstractmethod
    def __add__(self: S, other: S, /) -> S:
        ...

    @abstractmethod
    def __sub__(self: S, other: S, /) -> S:
        ...

    @abstractmethod
    def __mul__(self: S, other: float | int, /) -> S:
        ...


T = TypeVar('T', bound=_Interpable)


class Bamboo(Generic[T], metaclass=ABCMeta):
    @abstractmethod
    def __matmul__(self, time: float) -> T:
        ...

    @abstractmethod
    def __repr__(self) -> str:
        ...


def equal(a: float, b: float) -> float:
    return math.isclose(a, b)


class Segment(NamedTuple, Generic[T]):
    start: float
    end: float
    start_value: T
    end_value: T


class BrokenBamboo(Bamboo[T]):
    segments: list[Segment[T]]

    def __init__(self) -> None:
        super().__init__()
        self.segments = []

    def cut(self, start: float, end: float, start_value: T, end_value: T) -> None:
        bisect.insort_left(self.segments, Segment(start, end, start_value, end_value), key=lambda s: s.start)

    def __matmul__(self, time: float) -> T:
        right = bisect.bisect_left(self.segments, time, key=lambda s: s.start)
        if right < len(self.segments) and equal(self.segments[right].start, time):
            return self.segments[right].start_value
        seg = self.segments[right - 1]
        t = (time - seg.start) / (seg.end - seg.start)
        return seg.start_value + (seg.end_value - seg.start_value) * t

    def __repr__(self) -> str:
        if self.segments:
            return f'''BrokenBamboo(segments={len(self.segments)})'''
        return 'BrokenBamboo(empty)'


class Joint(NamedTuple, Generic[T]):
    timestamp: float
    value: T
    easing: EasingFunction


# TODO timestamp全换成整数
# TODO 修复overlap的问题
class LivingBamboo(Bamboo[T]):
    joints: list[Joint[T]]

    def __init__(self) -> None:
        self.joints = []

    def cut(self, timestamp: float, value: T, easing: EasingFunction | None = None) -> None:
        easing = easing or LVALUE
        insert_point = bisect.bisect_left(self.joints, timestamp, key=lambda j: j.timestamp)
        if not self.joints:
            self.joints.append(Joint(timestamp, value, easing))
            return

        # 处理浮点数精度问题
        # 相近的timestamp合并成一个
        if insert_point == len(self.joints):
            if equal(self.joints[insert_point - 1].timestamp, timestamp):
                self.joints[insert_point - 1] = self.joints[insert_point - 1]._replace(value=value, easing=easing)
                return
        else:
            if equal(self.joints[insert_point].timestamp, timestamp):
                self.joints[insert_point] = self.joints[insert_point]._replace(value=value, easing=easing)
                return
            elif insert_point > 0 and equal(self.joints[insert_point - 1].timestamp, timestamp):
                self.joints[insert_point - 1] = self.joints[insert_point - 1]._replace(value=value, easing=easing)
                return

        self.joints.insert(insert_point, Joint(timestamp, value, easing))

    def embed(self, start: float, end: float, end_value: T, easing: EasingFunction) -> None:
        # assert start < end
        insert_point = bisect.bisect_left(self.joints, start, key=lambda j: j.timestamp)
        if insert_point < len(self.joints) and equal(self.joints[insert_point].timestamp, start):
            # 更新起点记录，插入终点记录
            left_easing = self.joints[insert_point].easing
            self.joints[insert_point] = self.joints[insert_point]._replace(easing=easing)
            # assert (
            #     insert_point >= len(self.joints) - 1
            #     or self.joints[insert_point + 1].timestamp >= end
            #     or equal(self.joints[insert_point + 1].timestamp, end)
            # )
            if insert_point >= len(self.joints) - 1 or not equal(self.joints[insert_point + 1].timestamp, end):
                self.joints.insert(insert_point + 1, Joint(end, end_value, left_easing))
        elif insert_point == len(self.joints):
            # 在尾部插入起点记录和终点记录
            value = self.joints[-1].value  # 继承上个值
            self.joints.append(Joint(start, value, easing))
            self.joints.append(Joint(end, end_value, self.joints[-2].easing))
        else:
            # 在中间插入起点记录，视情况更新现有终点记录/插入终点记录
            # assert self.joints[insert_point].timestamp >= end or equal(self.joints[insert_point].timestamp, end)
            if equal(self.joints[insert_point].timestamp, end):
                self.joints[insert_point] = self.joints[insert_point]._replace(value=end_value)
                self.joints.insert(insert_point, Joint(start, self.joints[insert_point - 1].value, easing))
            else:
                left_easing = self.joints[insert_point - 1].easing
                self.joints.insert(insert_point, Joint(end, end_value, left_easing))
                self.joints.insert(insert_point, Joint(start, self.joints[insert_point - 1].value, easing))

    def __matmul__(self, time: float) -> T:
        right = bisect.bisect_left(self.joints, time, key=lambda j: j.timestamp)
        left = right - 1
        if right == len(self.joints):
            return self.joints[left].value
        if self.joints[right].timestamp == time or right == 0:
            return self.joints[right].value
        start = self.joints[left]
        end = self.joints[right]
        t = start.easing((time - start.timestamp) / (end.timestamp - start.timestamp))
        return start.value + (end.value - start.value) * t

    def __repr__(self) -> str:
        if self.joints:
            return f'''LivingBamboo(min={self.joints[0].timestamp}, max={self.joints[-1].timestamp}, total_joints={len(self.joints)})'''
        return 'LivingBamboo(empty)'


class TwinBamboo(Bamboo[complex]):
    xs: Bamboo[float]
    ys: Bamboo[float]
    convert: Callable[[complex], complex] | None

    def __init__(
        self, xs: Bamboo[float], ys: Bamboo[float], convert: Callable[[complex], complex] | None = None
    ) -> None:
        super().__init__()
        self.xs = xs
        self.ys = ys
        self.convert = convert

    def __matmul__(self, time: float) -> complex:
        if self.convert:
            return self.convert(complex(self.xs @ time, self.ys @ time))
        return complex(self.xs @ time, self.ys @ time)

    def __repr__(self) -> str:
        return f'TwinBamboo(xs={self.xs}, ys={self.ys})'


class BambooGrove(Bamboo[T]):
    bamboos: list[Bamboo[T]]
    zero: T

    def __init__(self, bamboos: list[Bamboo[T]], zero: T) -> None:
        super().__init__()
        self.bamboos = bamboos
        self.zero = zero

    def __matmul__(self, time: float) -> T:
        return sum((b @ time for b in self.bamboos), self.zero)

    def __repr__(self) -> str:
        return f'BambooGrove(of {len(self.bamboos)} bamboos)'


class BambooShoot(Bamboo[T]):
    const: T

    def __init__(self, const: T) -> None:
        super().__init__()
        self.const = const

    def __matmul__(self, time: float) -> T:
        return self.const

    def __repr__(self) -> str:
        return f'BambooShoot({self.const})'

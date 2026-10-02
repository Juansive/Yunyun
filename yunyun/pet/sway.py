"""被拎起来时的摆动：像拎着小猫走——往右拖，身子往左甩；停下来，还要来回荡几下才停稳。

这是一个不依赖 Qt 的小物理模型（弹簧 + 阻尼），方便单独测试：
1. 拖动的水平速度决定"想摆到多大角度"（拖得越快甩得越开，有上限）；
2. 实际角度像挂在弹簧上一样追过去：会冲过头一点再荡回来，阻尼让它慢慢停下。

每一帧调用一次 update(窗口的 x 坐标, 距上一帧的秒数)，得到当前角度。
"""


class Sway:
    MAX_ANGLE = 10.0     # 想摆到的最大角度（度）；弹簧冲过头时会比它稍大一点
    LIMIT = 15.0         # 无论如何不超过这个角度（再大身体就会被窗口边缘切掉）
    GAIN = 0.012         # 每 1 像素/秒的拖动速度，想摆多少度（拖 800 像素/秒 ≈ 10 度）
    STIFFNESS = 120.0    # 弹簧有多硬：越大摆得越快
    DAMPING = 6.5        # 阻尼：越大越快停稳；太小会一直晃（6.5：停下后来回荡一两下，约 1.5 秒停稳）
    SMOOTHING = 0.3      # 速度的平滑程度：鼠标事件来得不均匀，直接用速度会抖

    def __init__(self):
        self.reset()

    def reset(self, x=None):
        """回到静止（比如放下的时候）。x：此刻窗口的水平位置。"""
        self.angle = 0.0       # 当前角度（度）。正数 = 身子往左甩（往右拖的时候）
        self._velocity = 0.0   # 角速度（度/秒）
        self._target = 0.0     # 想摆到的角度（平滑过的）
        self._last_x = x

    def update(self, x, dt):
        """前进 dt 秒，返回新的角度。"""
        if self._last_x is None or dt <= 0:
            self._last_x = x
            return self.angle
        speed = (x - self._last_x) / dt           # 水平速度（像素/秒），往右为正
        self._last_x = x
        wanted = max(-self.MAX_ANGLE, min(self.MAX_ANGLE, speed * self.GAIN))
        self._target += (wanted - self._target) * self.SMOOTHING
        # 弹簧把角度拉向目标，阻尼和角速度方向相反、让摆动慢慢变小（就是物理课上的"阻尼振动"）
        acceleration = self.STIFFNESS * (self._target - self.angle) - self.DAMPING * self._velocity
        self._velocity += acceleration * dt
        self.angle = max(-self.LIMIT, min(self.LIMIT, self.angle + self._velocity * dt))
        return self.angle

    @property
    def resting(self):
        """几乎停稳了（不用再重绘）。"""
        return abs(self.angle) < 0.05 and abs(self._velocity) < 0.5 and abs(self._target) < 0.05

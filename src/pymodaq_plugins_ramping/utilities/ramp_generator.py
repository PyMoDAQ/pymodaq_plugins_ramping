import numpy as np


class RampGenerator:
    def __init__(self, start=0.0, end=10.0, duration=5.0):
        self.start = start
        self.end = end
        self.duration = duration

    def __call__(self, elapsed_time: float) -> float:
        return self.update(elapsed_time)

    def update(self, elapsed_time):
        # Clamp elapsed time to the valid duration window
        t_clamped = np.clip(elapsed_time, 0.0, self.duration)

        # Linear interpolation for the ramp output
        if self.duration > 0:
            fraction = t_clamped / self.duration
            value = self.start + (self.end - self.start) * fraction
        else:
            value = self.end

        return value
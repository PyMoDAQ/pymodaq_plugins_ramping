import numpy as np


class VariableRamp:
    def __init__(self, start=0.0, end=10.0, duration=5.0, time_scale=1.0):
        self.start = start
        self.end = end
        self.duration = duration  # Duration in the variable's own timescale
        self.time_scale = time_scale  # Rate relative to master time (e.g. 2.0 = runs twice as fast)
        self.local_time = 0.0  # Tracks elapsed time in its own scale
        self.value = start

    def update(self, dt_master):
        # Advance local time using its unique timescale factor
        self.local_time += dt_master * self.time_scale

        # Clamp local time to the valid duration window
        t_clamped = np.clip(self.local_time, 0.0, self.duration)

        # Linear interpolation for the ramp output
        if self.duration > 0:
            fraction = t_clamped / self.duration
            self.value = self.start + (self.end - self.start) * fraction
        else:
            self.value = self.end

        return self.value
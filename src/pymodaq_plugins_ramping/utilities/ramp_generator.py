import numpy as np
from pymodaq_data import Q_

class RampGenerator:
    def __init__(self,
                 start: float,
                 end: float,
                 duration=Q_(5.0,'s')):
        self.start = start
        self.end = end
        self.duration = duration

    def __call__(self, elapsed_time: Q_) -> float:
        return self.update(elapsed_time)

    def update(self, elapsed_time: Q_):
        # Clamp elapsed time to the valid duration window
        t_clamped = np.clip(elapsed_time, 0.0, self.duration)

        # Linear interpolation for the ramp output
        if self.duration.to_reduced_units().magnitude > 0:
            fraction = (t_clamped / self.duration).to_reduced_units().magnitude
            value = self.start + (self.end - self.start) * fraction
        else:
            value = self.end

        return value
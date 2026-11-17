"""Decides when a spoken command starts and ends, from per-frame "is this speech?" flags.

Pure logic (no audio libraries) so it can be unit tested.
"""
from dataclasses import dataclass

WAITING, SPEAKING, DONE, TIMEOUT = "waiting", "speaking", "done", "timeout"


@dataclass
class EndpointSettings:
    frame_ms: int = 20
    start_timeout_ms: int = 5000     # give up if no speech starts in this time
    min_speech_ms: int = 200         # this much (nearly) continuous speech counts as "clear speech"
    end_silence_ms: int = 800        # this much silence after speech ends the command
    max_ms: int = 15000              # hard cap on a command's length


class Endpointer:
    def __init__(self, s: EndpointSettings):
        self.s = s
        self.state = WAITING
        self.elapsed = 0
        self.run = 0          # speech ms in the current run (before start)
        self.gap = 0          # tolerated short gaps inside the starting run
        self.silence = 0
        self.speech_ms = 0

    def feed(self, is_speech: bool) -> str:
        if self.state in (DONE, TIMEOUT):
            return self.state
        s = self.s
        self.elapsed += s.frame_ms
        if self.state == WAITING:
            if is_speech:
                self.run += s.frame_ms
                self.gap = 0
                if self.run >= s.min_speech_ms:
                    self.state = SPEAKING
                    self.speech_ms = self.run
            else:
                self.gap += s.frame_ms
                if self.gap > 60:            # a real pause: the run wasn't clear speech
                    self.run = 0
            if self.state == WAITING and self.elapsed >= s.start_timeout_ms:
                self.state = TIMEOUT
            return self.state
        # SPEAKING
        if is_speech:
            self.silence = 0
            self.speech_ms += s.frame_ms
        else:
            self.silence += s.frame_ms
            if self.silence >= s.end_silence_ms:
                self.state = DONE
        if self.elapsed >= s.start_timeout_ms + s.max_ms or self.speech_ms + self.silence >= s.max_ms:
            self.state = DONE
        return self.state

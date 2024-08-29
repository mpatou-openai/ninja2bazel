from dataclasses import dataclass


@dataclass
class VisitorContext:
    parentIsPhony: bool

    def __post_init__(self):
        self.parentIsPhony = False
        self.producer = None

    def setup_subcontext(self) -> "VisitorContext":
        newCtx = VisitorContext(**self.__dict__)
        return newCtx

    def cleanup(self) -> None:
        pass

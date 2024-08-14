from dataclasses import dataclass


@dataclass
class VisitorContext:
    def setup_subcontext(self) -> "VisitorContext":
        newCtx = VisitorContext(**self.__dict__)
        return newCtx

    def cleanup(self) -> None:
        pass

from pathlib import Path


class PrivateFileStorage:
    """Keys are server-generated; access is authorized by the owning business."""
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, key):
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root): raise ValueError('Invalid private storage key')
        return path

    def put(self, key, data):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as output:
            output.write(data)
        return path

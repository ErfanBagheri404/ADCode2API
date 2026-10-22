"""Configuration."""
import os


class Config:
    def __init__(self, args=None):
        self.port = 61024
        self.bind = "127.0.0.1"
        self.api_key = None
        self.log_path = None
        self.base_url = os.environ.get("ADCODE2API_UPSTREAM", "https://adcode.bluethenics.com")
        if args:
            self.port = getattr(args, "port", self.port)
            self.bind = getattr(args, "bind", self.bind)
            self.api_key = getattr(args, "api_key", self.api_key)
            self.log_path = getattr(args, "log", self.log_path)
        self.port = int(os.environ.get("ADCODE2API_PORT", self.port))


def load_config(args=None):
    return Config(args)

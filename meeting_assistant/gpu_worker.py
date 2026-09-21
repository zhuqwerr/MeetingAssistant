"""Isolate native Windows CUDA lifetime from the meeting server.

Only audio copies and decoder results cross this local pipe. The parent owns all
recordings and storage. Windows CUDA cleanup can crash after successful decoding;
the worker exits without running native destructors after acknowledging close.
"""
import multiprocessing
import os


def _run(connection, settings):
    try:
        from .asr import Transcriber
        loader = Transcriber()
        model = loader._load_native(settings)
        connection.send((True, None))
        while True:
            command, payload = connection.recv()
            if command == "close":
                connection.send((True, None))
                break
            samples, options = payload
            try:
                segments, _ = model.transcribe(samples, **options)
                connection.send((True, list(segments)))
            except Exception as exc:
                connection.send((False, str(exc)))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            connection.send((False, str(exc)))
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()
        os._exit(0)


class GPUModel:
    def __init__(self, settings):
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_run, args=(child, settings), daemon=True)
        self.process.start()
        child.close()
        try:
            self._receive(180)
        except Exception:
            self.close()
            raise

    def _receive(self, timeout):
        if not self.connection.poll(timeout):
            raise RuntimeError("GPU 识别进程超时，请重新准备模型")
        try:
            success, result = self.connection.recv()
        except EOFError as exc:
            raise RuntimeError("GPU 识别进程已退出，请重新准备模型") from exc
        if not success:
            raise RuntimeError(result)
        return result

    def transcribe(self, samples, **options):
        self.connection.send(("transcribe", (samples, options)))
        return self._receive(120), None

    def close(self):
        if self.process.is_alive():
            try:
                self.connection.send(("close", None))
                self.process.join(3)
            except (BrokenPipeError, EOFError, OSError):
                pass
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(3)
        self.connection.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

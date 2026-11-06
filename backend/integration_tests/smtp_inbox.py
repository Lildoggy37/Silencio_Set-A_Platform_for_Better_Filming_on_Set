"""A loopback-only SMTP inbox for integration tests; no relay or real recipients."""
import socketserver
import threading


class LocalInbox:
    def __init__(self):
        self.messages = []
        self.condition = threading.Condition()
        inbox = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(5)

                def reply(text):
                    self.wfile.write(text + b'\r\n')
                    self.wfile.flush()

                reply(b'220 localhost integration SMTP')
                while line := self.rfile.readline(65536):
                    command = line.upper()
                    if command.startswith((b'EHLO', b'HELO')):
                        reply(b'250-localhost\r\n250 SIZE 1024000')
                    elif command.startswith((b'MAIL FROM', b'RCPT TO', b'RSET')):
                        reply(b'250 OK')
                    elif command.startswith(b'DATA'):
                        reply(b'354 End with dot')
                        body = bytearray()
                        while item := self.rfile.readline(65536):
                            if item == b'.\r\n':
                                break
                            body.extend(item[1:] if item.startswith(b'..') else item)
                        with inbox.condition:
                            inbox.messages.append(bytes(body))
                            inbox.condition.notify_all()
                        reply(b'250 accepted')
                    elif command.startswith(b'QUIT'):
                        reply(b'221 closing')
                        return
                    else:
                        reply(b'500 unsupported')

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        self.server = Server(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':0.05}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def wait_for(self, count):
        with self.condition:
            assert self.condition.wait_for(lambda:len(self.messages) >= count, timeout=20), 'SMTP delivery timed out'

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

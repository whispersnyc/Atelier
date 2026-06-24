from bottle import Bottle, ServerAdapter
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from socketserver import ThreadingMixIn

PORT = 8767
app  = Bottle()

class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

class _ThreadedServer(ServerAdapter):
    def run(self, handler):
        srv = make_server(self.host, self.port, handler,
                          server_class=_ThreadingWSGIServer,
                          handler_class=WSGIRequestHandler)
        srv.serve_forever()

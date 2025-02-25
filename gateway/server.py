import os
import json
from flask import Flask, request, send_file
from flask_pymongo import PyMongo
from pymongo import MongoClient
from gridfs import GridFS
import pika
from bson.objectid import ObjectId
import socket
import asyncio
import aiohttp
from aiohttp import web
import motor.motor_asyncio

# Import your auth and storage modules
from auth import validate
from auth_svc import access
from storage import util

# Initialize the Flask app
server = Flask(__name__)

# MongoDB connections
mongo_video = PyMongo(server, uri="mongodb://host.minikube.internal:27017/videos")
mongo_mp3 = PyMongo(server, uri="mongodb://host.minikube.internal:27017/mp3s") #same mongo diff uri

# GridFS instances
fs_videos = GridFS(mongo_video.db)
fs_mp3s = GridFS(mongo_mp3.db)

# RabbitMQ connection
connection = pika.BlockingConnection(pika.ConnectionParameters("rabbitmq"))
channel = connection.channel()

# DNS override function
_original_getaddrinfo = socket.getaddrinfo

def override_dns(overrides: dict):
    def custom_getaddrinfo(hostname, *args, **kwargs):
        if hostname in overrides:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (overrides[hostname], 80))]
        return _original_getaddrinfo(hostname, *args, **kwargs)
    socket.getaddrinfo = custom_getaddrinfo

def restore_dns():
    socket.getaddrinfo = _original_getaddrinfo

# Asynchronous routes
@server.route("/login", methods=["POST"])
async def login():
    token, err = await access.login(request)
    if not err:
        return token
    else:
        return err

@server.route("/upload", methods=["POST"])
async def upload():
    access, err = await validate.token(request)
    if err:
        return err

    access = json.loads(access)
    if access["admin"]:
        if len(request.files) != 1:
            return "exactly 1 file required", 400

        for _, f in request.files.items():
            err = await util.upload(f, fs_videos, channel, access)
            if err:
                return err

        return "success!", 200
    else:
        return "not authorized", 401

@server.route("/download", methods=["GET"])
async def download():
    access, err = await validate.token(request)
    if err:
        return err

    access = json.loads(access)
    if access["admin"]:
        fid_string = request.args.get("fid")
        if not fid_string:
            return "fid is required", 400

        try:
            out = await fs_mp3s.get(ObjectId(fid_string))
            return await send_file(out, download_name=f"{fid_string}.mp3")
        except Exception as err:
            print(err)
            return "internal server error", 500

    return "not authorized", 401


@server.route("/health")
async def health_check():
    # Check critical components
    try:
        # Check MongoDB connection
        mongo_video.db.command('ping')
        mongo_mp3.db.command('ping')

        # Check RabbitMQ connection
        connection.process_data_events()

        return "OK", 200
    except Exception as e:
        server.logger.error(f"Health check failed: {str(e)}")
        return "Service Unavailable", 503

if __name__ == "__main__":
    # DNS overrides
    overrides = {"mp3convertor.com": "127.0.0.1", "rabbitmq-manager-micro-app.com": "127.0.0.1"}
    #override_dns(overrides)

    # Run the Flask app
    server.run(host="0.0.0.0", port=8080)

    # Restore DNS when the app is shutting down
    #restore_dns()


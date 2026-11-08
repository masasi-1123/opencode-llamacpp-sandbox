# set up the container.
FROM python:latest

# set the working dir.
WORKDIR /app

# copy the app dir.
COPY /app /app

# install libraries.
RUN pip install --no-cache-dir fastapi uvicorn httpx

# expose the port.
EXPOSE 8000

# command to run the app using uvicorn.
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000","--reload"]

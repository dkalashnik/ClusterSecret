FROM dkalashnik/clustersecretbase:0.0.6
ADD /src /src
CMD kopf run -v --log-format=full /src/handlers.py

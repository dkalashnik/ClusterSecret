IMG_NAMESPACE = dkalashnik
IMG_NAME = clustersecretbase
IMG_FQNAME = $(IMG_NAMESPACE)/$(IMG_NAME)
IMG_VERSION = 0.0.6

.PHONY: container push clean
all: container push


container:
	docker build -t $(IMG_FQNAME):$(IMG_VERSION) -t $(IMG_FQNAME):latest .

push: container
	docker push $(IMG_FQNAME):$(IMG_VERSION)
	# Also update :latest
	docker push $(IMG_FQNAME):latest

clean:
	docker rmi $(IMG_FQNAME):$(IMG_VERSION)

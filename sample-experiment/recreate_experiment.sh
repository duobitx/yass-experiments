kubectl delete -f 01_hardware_specs.yaml
kubectl delete -f 02_layout.yaml
kubectl delete -f 03_experiment_defintion.yaml
kubectl delete -f 04_experiment.yaml



kubectl create -f 01_hardware_specs.yaml
kubectl create -f 02_layout.yaml
kubectl create -f 03_experiment_defintion.yaml
kubectl create -f 04_experiment.yaml

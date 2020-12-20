import torch
from pgd import Attacker
import argparse
from dataset_utils import create_data_loaders, Normalizer
from model_utils import MODELS_LIST, get_model, load_model


class Trainer:
    def __init__(self, training_args_dict, pgd_args_dict,
                 criterion=torch.nn.CrossEntropyLoss(),
                 optimizer=torch.optim.Adam):

        if training_args_dict['checkpoint_location'] is not None:
            self.model = load_model(location=training_args_dict['checkpoint_location'])
            training_args_dict['arch'] = self.model.arch
        else:
            self.model = get_model(arch=training_args_dict['arch'], pretrained=training_args_dict['pretrained'])

        self.normalize = Normalizer(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        self.training_args_dict = training_args_dict
        self.pgd_args_dict = pgd_args_dict
        self.adversarial = training_args_dict['adversarial']
        self.attacker = None
        self.criterion = criterion
        self.optimizer = optimizer(self.model.parameters(), lr=training_args_dict['learning_rate'])
        self.losses = []

    def fit(self, images, labels):
        for epoch in range(self.training_args_dict['epochs']):
            current_loss = 0.0
            images_loader, labels_loader = create_data_loaders(images, labels, shuffle=True)

            for images_batch, labels_batch in zip(images_loader, labels_loader):
                if self.adversarial:
                    images_batch = self.create_adversarial_examples(images_batch, labels_batch)

                self.model = self.model.cuda().train()
                predictions = self.model(self.normalize(images_batch.cuda()))

                self.optimizer.zero_grad()
                loss = self.criterion(predictions, labels_batch.cuda())
                loss.backward()
                self.optimizer.step()

                current_loss += loss.item() * images_batch.size(0)

            epoch_loss = current_loss / len(images)
            print('Epoch: {}/{} - Loss: {}'.format(str(epoch+1),
                                                   str(self.training_args_dict['epochs']),
                                                   str(epoch_loss)))

            self.losses.append(epoch)

    def create_adversarial_examples(self, images_batch, labels_batch):
        if self.attacker is None:
            self.attacker = Attacker(self.model.cpu().eval(), self.pgd_args_dict)

        self.attacker.model = self.model.cpu().eval()

        mask = None
        adversarial_batch = None

        for image, label in zip(images_batch, labels_batch):
            if mask is None:
                mask = torch.ones(image.size())

            if adversarial_batch is None:
                adversarial_batch = self.attacker(image=image, mask=mask, target=label, random_start=True).unsqueeze(0)
                continue

            adversarial_example = self.attacker(image=image, mask=mask, target=label, random_start=True).unsqueeze(0)
            adversarial_batch = torch.cat((adversarial_batch, adversarial_example), 0)

        return adversarial_batch

    def serialize(self):
        torch.save({'state_dict': self.model.state_dict(),
                    'training_args': self.training_args_dict,
                    'pgd_args': self.pgd_args_dict,
                    'losses': self.losses},
                   self.training_args_dict['save_file_name'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--arch', type=str, choices=MODELS_LIST, default='resnet50')
    parser.add_argument('--pretrained', default=False, action='store_true')
    parser.add_argument('--checkpoint_location', type=str, default=None)
    parser.add_argument('--epochs', type=int, default=10)
    parser.add_argument('--learning_rate', type=float, default=1e-2)
    parser.add_argument('--adversarial', default=False, action='store_true')
    parser.add_argument('--save_file_name', type=str, default='models/resnet50_robust.pt')
    args_dict = vars(parser.parse_args())

    pgd_args_dict = {
        'arch': args_dict['arch'],
        'dataset': 'dataset/imagenet-airplanes-images.pt',
        'masks': False,
        'eps': 32/255.0,
        'norm': 'linf',
        'step_size': 16/255.0,
        'num_iterations': 1,
        'targeted': False,
        'eot': False,
        'transfer': False,
    }

    images = torch.load('dataset/imagenet-airplanes-images.pt')
    labels = torch.load('dataset/imagenet-airplanes-labels.pt')

    trainer = Trainer(args_dict, pgd_args_dict)
    trainer.fit(images, labels)
    trainer.serialize()


if __name__ == '__main__':
    main()
